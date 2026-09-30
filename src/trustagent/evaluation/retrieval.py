"""Retrieval evaluation: Recall@K and MRR on hand-labelled questions, per search mode.

A result is relevant when its document and section match a labelled answer (labels name sections,
not chunk ids, so they survive re-ingestion). Query embeddings are cached so every mode sees the
same vector and the free-tier quota isn't spent three times.
"""

import datetime as dt
import json
from typing import Any

from sqlalchemy.orm import Session

from trustagent.config import PROJECT_ROOT
from trustagent.evaluation.metrics import mean_reciprocal_rank, recall_at_k
from trustagent.rag.embeddings import Embedder
from trustagent.rag.retrieve import search

MODES = ("hybrid", "vector", "keyword")


class CachedEmbedder:
    def __init__(self, inner: Embedder):
        self.inner, self.model, self.dim = inner, inner.model, inner.dim
        self._cache: dict[str, list[float]] = {}

    def embed_query(self, text: str) -> list[float]:
        if text not in self._cache:
            self._cache[text] = self.inner.embed_query(text)
        return self._cache[text]

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return self.inner.embed_documents(texts)


def load_questions() -> list[dict[str, Any]]:
    return json.loads((PROJECT_ROOT / "evals" / "retrieval_questions.json").read_text(encoding="utf-8"))


def is_relevant(hit, relevant: list[dict[str, str]]) -> bool:
    return any(hit.document_id == r["document_id"] and r["section"] in (hit.section or "") for r in relevant)


def evaluate(session: Session, embedder: Embedder, k: int = 5) -> dict[str, Any]:
    cached = CachedEmbedder(embedder)
    questions = load_questions()
    out: dict[str, Any] = {"questions": len(questions), "modes": {}, "misses": {}}
    for mode in MODES:
        ranked = []
        for q in questions:
            hits = search(session, q["question"], cached, k=k, mode=mode, doc_type=q.get("doc_type"),
                          supplier_id=q.get("supplier_id"),
                          as_of=dt.date.fromisoformat(q["as_of"]) if q.get("as_of") else None)  # fmt: skip
            ranked.append([is_relevant(h, q["relevant"]) for h in hits])
        out["modes"][mode] = {
            "recall@1": recall_at_k(ranked, 1), "recall@3": recall_at_k(ranked, 3), "recall@5": recall_at_k(ranked, 5),
            "mrr": mean_reciprocal_rank(ranked),
        }  # fmt: skip
        out["misses"][mode] = [q["id"] for q, r in zip(questions, ranked, strict=True) if not any(r[:3])]
    return out
