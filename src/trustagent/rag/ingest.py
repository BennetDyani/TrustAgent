"""Ingestion pipeline: PDF -> pages -> clean text -> structure-aware chunks -> embeddings -> pgvector.

Each stage is a separate function with an inspectable output (notebook 03
shows them one at a time). Metadata comes from ``data/documents_manifest.json``,
never from the model.

Run with:  uv run python -m trustagent.rag.ingest
"""

import datetime as dt
import json
import re
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

from pypdf import PdfReader
from sqlalchemy import delete
from sqlalchemy.orm import Session

from trustagent.config import get_settings
from trustagent.db.models import DocumentChunkRow
from trustagent.rag.embeddings import Embedder

# "5 Supplier Bank Account Changes (POL-001)", "5.2 Verification procedure", "Appendix A - Red Flags", "C.1 ..."
# The title must start with a capitalised word, so table cells like "5 (POL-001)" are not headings.
_HEADING = re.compile(r"^(?:\d+(?:\.\d+)?|Appendix [A-Z]\s*-|[A-Z]\.\d+)\s+[A-Z][A-Za-z].{0,80}$")
_CLAUSE = re.compile(r"^\d+(?:\.\d+)+\s")  # "5.2.1 On receiving ..." starts a new paragraph
_PAGE_NUMBER = re.compile(r"^page \d+( of \d+)?$", re.IGNORECASE)


@dataclass
class DocumentMeta:
    file: str
    document_id: str
    doc_type: str
    title: str
    supplier_id: str | None = None
    contract_id: str | None = None
    effective_from: dt.date | None = None
    effective_to: dt.date | None = None


@dataclass
class Page:
    number: int
    lines: list[str]


@dataclass
class Chunk:
    text: str  # includes the contextual header "title | section"
    section: str
    page: int
    index: int = 0
    embedding: list[float] = field(default_factory=list)


def load_manifest(path: Path | None = None) -> list[DocumentMeta]:
    path = path or get_settings().data_dir / "documents_manifest.json"
    out = []
    for d in json.loads(path.read_text(encoding="utf-8")):
        out.append(DocumentMeta(
            file=d["file"], document_id=d["document_id"], doc_type=d["doc_type"], title=d["title"],
            supplier_id=d.get("supplier_id"), contract_id=d.get("contract_id"),
            effective_from=dt.date.fromisoformat(d["effective_from"]) if d.get("effective_from") else None,
            effective_to=dt.date.fromisoformat(d["effective_to"]) if d.get("effective_to") else None,
        ))  # fmt: skip
    return out


# --- stage 1: extract ------------------------------------------------------------------------------


def extract_pages(pdf_path: Path) -> list[Page]:
    reader = PdfReader(str(pdf_path))
    return [
        Page(number=i + 1, lines=[ln.strip() for ln in (p.extract_text() or "").splitlines() if ln.strip()])
        for i, p in enumerate(reader.pages)
    ]


# --- stage 2: clean ----------------------------------------------------------------------------------


def strip_boilerplate(pages: list[Page], min_share: float = 0.6) -> list[Page]:
    """Remove running headers/footers: lines that repeat on most pages, and bare page numbers."""
    if len(pages) >= 2:
        counts = Counter(line for p in pages for line in set(p.lines))
        repeated = {line for line, n in counts.items() if n / len(pages) >= min_share}
    else:
        repeated = set()
    return [Page(p.number, [ln for ln in p.lines if ln not in repeated and not _PAGE_NUMBER.match(ln)]) for p in pages]


# --- stage 3: chunk -----------------------------------------------------------------------------------


def _is_heading(line: str) -> bool:
    return bool(_HEADING.match(line)) and not line.endswith((".", ",", ";", ":")) and len(line) <= 90


def _sections(pages: list[Page]) -> list[tuple[str, int, list[str]]]:
    """Split into (section title, start page, paragraphs). Top-level headings reset the sub-heading."""
    sections: list[tuple[str, int, list[str]]] = []
    top, title, start, paras = "", "Preamble", pages[0].number if pages else 1, []
    current: list[str] = []

    def flush_para():
        if current:
            paras.append(" ".join(current))
            current.clear()

    for page in pages:
        for line in page.lines:
            if _is_heading(line):
                flush_para()
                if paras:
                    sections.append((title, start, list(paras)))
                paras.clear()
                is_sub = bool(re.match(r"^(\d+\.\d+|[A-Z]\.\d+)\s", line))
                top = top if is_sub else line
                title, start = (f"{top} > {line}" if is_sub and top else line), page.number
                continue
            if _CLAUSE.match(line) or line.startswith(("Contract number:", "Document:")):
                flush_para()
            current.append(line)
    flush_para()
    if paras:
        sections.append((title, start, list(paras)))
    return sections


def chunk_document(
    pages: list[Page], meta: DocumentMeta, size: int | None = None, overlap: int | None = None
) -> list[Chunk]:
    """Structure-aware chunking: sections first, then paragraphs packed up to ``size`` characters.

    A section that fits is one chunk. A longer one is split on paragraph
    boundaries, carrying the last paragraph (up to ``overlap`` chars) into
    the next chunk. Every chunk starts with a "title | section" header, which
    helps both the embedding and keyword search.
    """
    s = get_settings()
    size, overlap = size or s.chunk_size, overlap if overlap is not None else s.chunk_overlap
    chunks: list[Chunk] = []
    for title, page, paras in _sections(pages):
        header = f"{meta.title} ({meta.document_id}) | {title}\n"
        batch: list[str] = []
        for para in paras:
            if batch and len(header) + sum(len(p) + 1 for p in batch) + len(para) > size:
                chunks.append(Chunk(header + "\n".join(batch), title, page))
                tail = batch[-1] if len(batch[-1]) <= overlap else batch[-1][-overlap:]
                batch = [tail]
            batch.append(para)
        if batch:
            chunks.append(Chunk(header + "\n".join(batch), title, page))
    for i, c in enumerate(chunks):
        c.index = i
    return chunks


# --- stages 4-5: embed and store ------------------------------------------------------------------------


def embed_chunks(chunks: list[Chunk], embedder: Embedder) -> list[Chunk]:
    vectors = embedder.embed_documents([c.text for c in chunks])
    for c, v in zip(chunks, vectors, strict=True):
        c.embedding = v
    return chunks


def store_chunks(session: Session, meta: DocumentMeta, chunks: list[Chunk], embedder: Embedder) -> int:
    """Replace this document's chunks (idempotent re-ingestion)."""
    session.execute(delete(DocumentChunkRow).where(DocumentChunkRow.document_id == meta.document_id))
    for c in chunks:
        session.add(DocumentChunkRow(
            document_id=meta.document_id, source=Path(meta.file).name, doc_type=meta.doc_type,
            supplier_id=meta.supplier_id, contract_id=meta.contract_id, section=c.section[:300], page=c.page,
            effective_from=meta.effective_from, effective_to=meta.effective_to, chunk_index=c.index,
            content=c.text, embedding_model=embedder.model, embedding=c.embedding,
        ))  # fmt: skip
    session.flush()
    return len(chunks)


def ingest_document(session: Session, meta: DocumentMeta, embedder: Embedder) -> list[Chunk]:
    pages = strip_boilerplate(extract_pages(get_settings().data_dir / meta.file))
    chunks = embed_chunks(chunk_document(pages, meta), embedder)
    store_chunks(session, meta, chunks, embedder)
    return chunks


def ingest_all(session: Session, embedder: Embedder, manifest: list[DocumentMeta] | None = None) -> dict[str, int]:
    return {m.document_id: len(ingest_document(session, m, embedder)) for m in manifest or load_manifest()}


def main() -> None:
    from trustagent.db.session import check_embedding_dimension, get_engine, session_scope
    from trustagent.rag.embeddings import GeminiEmbedder

    check_embedding_dimension(get_engine())  # fail fast on a model/column mismatch
    with session_scope() as session:
        counts = ingest_all(session, GeminiEmbedder())
    for doc, n in counts.items():
        print(f"{doc}: {n} chunks")


if __name__ == "__main__":
    main()
