"""Hybrid retrieval: metadata filter -> vector search + full-text search -> Reciprocal Rank Fusion.

- **Filter first.** For contracts: this supplier only, and only the agreement
  in force on the invoice date (an expired contract's older rates must never
  be used; policy manual 10.3).
- **Two retrievers.** pgvector cosine distance, and Postgres full-text search
  (``tsvector`` with OR semantics so a long question still matches).
- **RRF, k=60 (bug #7).** Each list contributes ``1 / (k + rank)``. Raw cosine
  and ``ts_rank`` scores are on different scales and are never added.
- ``mode`` = "hybrid" | "vector" | "keyword", so the evaluation can compare them.
"""

import datetime as dt
import re
from dataclasses import dataclass
from typing import Literal

from sqlalchemy import Select, func, literal_column, or_, select
from sqlalchemy.orm import Session

from trustagent.config import get_settings
from trustagent.db.models import DocumentChunkRow
from trustagent.domain import Citation
from trustagent.rag.embeddings import Embedder

Mode = Literal["hybrid", "vector", "keyword"]


@dataclass
class RetrievedChunk:
    id: int
    document_id: str
    source: str
    doc_type: str
    supplier_id: str | None
    contract_id: str | None
    section: str | None
    page: int | None
    content: str
    score: float
    vector_rank: int | None = None
    keyword_rank: int | None = None

    @property
    def citation(self) -> Citation:
        return Citation(source=self.source, page=self.page, section=self.section)


def _filtered(stmt: Select, doc_type: str | None, supplier_id: str | None, as_of: dt.date | None) -> Select:
    if doc_type:
        stmt = stmt.where(DocumentChunkRow.doc_type == doc_type)
    if supplier_id:
        stmt = stmt.where(DocumentChunkRow.supplier_id == supplier_id)
    if as_of:
        stmt = stmt.where(
            or_(DocumentChunkRow.effective_from.is_(None), DocumentChunkRow.effective_from <= as_of),
            or_(DocumentChunkRow.effective_to.is_(None), DocumentChunkRow.effective_to >= as_of),
        )
    return stmt


def _or_tsquery(query: str) -> str:
    """Words joined with OR, for to_tsquery. Alphanumerics only, so user text can't inject tsquery syntax."""
    words = re.findall(r"[A-Za-z0-9]+", query)
    return " | ".join(dict.fromkeys(w.lower() for w in words))


def rrf_merge(ranked_lists: list[list[int]], k: int) -> dict[int, float]:
    """Reciprocal Rank Fusion over lists of ids (best first)."""
    scores: dict[int, float] = {}
    for ranked in ranked_lists:
        for rank, chunk_id in enumerate(ranked, start=1):
            scores[chunk_id] = scores.get(chunk_id, 0.0) + 1.0 / (k + rank)
    return scores


def search(
    session: Session,
    query: str,
    embedder: Embedder | None,
    *,
    k: int | None = None,
    mode: Mode = "hybrid",
    doc_type: str | None = None,
    supplier_id: str | None = None,
    as_of: dt.date | None = None,
    candidates: int = 20,
) -> list[RetrievedChunk]:
    s = get_settings()
    k = k or s.retrieval_top_k
    vector_ids: list[int] = []
    keyword_ids: list[int] = []

    if mode in ("hybrid", "vector"):
        if embedder is None:
            raise ValueError("vector search needs an embedder")
        qvec = embedder.embed_query(query)
        stmt = select(DocumentChunkRow.id).order_by(DocumentChunkRow.embedding.cosine_distance(qvec)).limit(candidates)
        vector_ids = list(session.scalars(_filtered(stmt, doc_type, supplier_id, as_of)))

    if mode in ("hybrid", "keyword") and (terms := _or_tsquery(query)):
        tsq = func.to_tsquery(literal_column("'english'"), terms)
        stmt = (
            select(DocumentChunkRow.id)
            .where(DocumentChunkRow.content_tsv.op("@@")(tsq))
            .order_by(func.ts_rank_cd(DocumentChunkRow.content_tsv, tsq).desc(), DocumentChunkRow.id)
            .limit(candidates)
        )
        keyword_ids = list(session.scalars(_filtered(stmt, doc_type, supplier_id, as_of)))

    fused = rrf_merge([ids for ids in (vector_ids, keyword_ids) if ids], s.rrf_k)
    top = sorted(fused, key=lambda i: (-fused[i], i))[:k]
    if not top:
        return []
    rows = {r.id: r for r in session.scalars(select(DocumentChunkRow).where(DocumentChunkRow.id.in_(top)))}
    v_rank = {cid: n for n, cid in enumerate(vector_ids, start=1)}
    k_rank = {cid: n for n, cid in enumerate(keyword_ids, start=1)}
    return [
        RetrievedChunk(
            id=r.id,
            document_id=r.document_id,
            source=r.source,
            doc_type=r.doc_type,
            supplier_id=r.supplier_id,
            contract_id=r.contract_id,
            section=r.section,
            page=r.page,
            content=r.content,
            score=fused[r.id],
            vector_rank=v_rank.get(r.id),
            keyword_rank=k_rank.get(r.id),
        )  # fmt: skip
        for r in (rows[i] for i in top)
    ]
