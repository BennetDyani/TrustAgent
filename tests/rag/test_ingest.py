"""Ingestion stages: each output is inspectable and tested."""

import re

import pytest
from sqlalchemy import func, select

from trustagent.config import get_settings
from trustagent.db.models import DocumentChunkRow
from trustagent.rag.ingest import Page, chunk_document, extract_pages, load_manifest, strip_boilerplate

MANIFEST = {m.document_id: m for m in load_manifest()}


def pages_of(doc_id: str) -> list[Page]:
    return strip_boilerplate(extract_pages(get_settings().data_dir / MANIFEST[doc_id].file))


def test_manifest_lists_contracts_and_the_policy_manual():
    assert {m.doc_type for m in MANIFEST.values()} == {"contract", "policy"}
    assert MANIFEST["MC-2024-001"].effective_to.isoformat() == "2025-03-31"  # the expired one
    assert MANIFEST["FIN-POL-01"].supplier_id is None


def test_running_headers_footers_and_page_numbers_are_removed():
    raw = extract_pages(get_settings().data_dir / MANIFEST["FIN-POL-01"].file)
    assert any("Confidential - TrustCorp Holdings Ltd" in ln for ln in raw[3].lines)
    for page in pages_of("FIN-POL-01"):
        assert not any("Confidential - TrustCorp" in ln or re.fullmatch(r"Page \d+", ln) for ln in page.lines)


def test_policy_manual_is_ten_to_twenty_pages():
    assert 10 <= len(pages_of("FIN-POL-01")) <= 20


def test_chunks_follow_sections_and_carry_a_context_header():
    chunks = chunk_document(pages_of("FIN-POL-01"), MANIFEST["FIN-POL-01"])
    verification = [c for c in chunks if c.section.endswith("5.2 Verification procedure")]
    assert verification and verification[0].section.startswith("5 Supplier Bank Account Changes (POL-001)")
    assert verification[0].page == 3
    assert all(c.text.startswith("Procurement and Supplier Payments Policy Manual (FIN-POL-01) | ") for c in chunks)


def test_table_cells_are_not_mistaken_for_headings():
    sections = {c.section for c in chunk_document(pages_of("FIN-POL-01"), MANIFEST["FIN-POL-01"])}
    assert not any(re.match(r"^\d+ \(POL", s) for s in sections)
    assert "Appendix A - Red Flags Checklist" in sections


def test_no_text_is_lost_in_chunking():
    pages = pages_of("FIN-POL-01")
    body = " ".join(c.text for c in chunk_document(pages, MANIFEST["FIN-POL-01"]))
    for clause in ("5.2.2", "6.3 (Amended June 2026)", "10.3 Only the agreement in force", "18.1 If a payment"):
        assert clause in body


@pytest.mark.parametrize("size, overlap", [(400, 80), (800, 120)])
def test_chunk_size_and_overlap(size, overlap):
    chunks = chunk_document(pages_of("FIN-POL-01"), MANIFEST["FIN-POL-01"], size=size, overlap=overlap)
    longest_paragraph = 700  # a single clause is never split mid-sentence
    assert all(len(c.text) <= size + longest_paragraph for c in chunks)
    same_section = [(a, b) for a, b in zip(chunks, chunks[1:], strict=False) if a.section == b.section]
    assert same_section, "expected at least one section split into several chunks"
    for a, b in same_section:
        body_b = b.text.split("\n", 1)[1]
        assert body_b[:40] in a.text, "consecutive chunks of one section should overlap"


def test_contract_rate_table_stays_in_one_chunk():
    chunks = chunk_document(pages_of("ABC-2026-017"), MANIFEST["ABC-2026-017"])
    rates = [c for c in chunks if c.section == "4 Rates"]
    assert len(rates) == 1 and "R 3,950.00" in rates[0].text and "FlexiDesk Pro" in rates[0].text


# --- storage -----------------------------------------------------------------------------------


def test_ingestion_stores_every_document_and_is_idempotent(clean_db, embedder, ingested):
    from trustagent.rag.ingest import ingest_all

    assert set(ingested) == set(MANIFEST) and all(n > 0 for n in ingested.values())
    with clean_db() as s:
        first = s.scalar(select(func.count()).select_from(DocumentChunkRow))
    with clean_db() as s, s.begin():
        ingest_all(s, embedder)
    with clean_db() as s:
        assert s.scalar(select(func.count()).select_from(DocumentChunkRow)) == first
        row = s.scalars(select(DocumentChunkRow).where(DocumentChunkRow.document_id == "MC-2024-001")).first()
        assert row.embedding_model == embedder.model and len(row.embedding) == get_settings().embedding_dim
        assert (row.supplier_id, row.contract_id, row.doc_type) == ("SUP-002", "MC-2024-001", "contract")
