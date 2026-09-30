"""Retrieval: metadata filters, hybrid search with RRF, citations, refusal, context assembly."""

import datetime as dt

import pytest

from trustagent.graph.llm_steps import ReviewInput, review_messages
from trustagent.rag.context import rag_context
from trustagent.rag.retrieve import _or_tsquery, rrf_merge, search

# --- RRF (bug #7: merge ranks, never add raw scores) --------------------------------------------


def test_rrf_uses_ranks_only():
    fused = rrf_merge([[1, 2, 3], [3, 1]], k=60)
    assert fused[1] == pytest.approx(1 / 61 + 1 / 62)
    assert fused[3] == pytest.approx(1 / 63 + 1 / 61)
    assert fused[2] == pytest.approx(1 / 62)
    assert max(fused, key=fused.get) == 1  # in both lists, near the top of each


def test_item_found_by_both_retrievers_beats_one_found_by_either_alone():
    fused = rrf_merge([[10, 20], [30, 20]], k=60)
    assert max(fused, key=fused.get) == 20


def test_tsquery_is_sanitised():
    assert _or_tsquery("POL-001: 'bank' | & !change:*") == "pol | 001 | bank | change"


# --- search -----------------------------------------------------------------------------------------


def test_contract_search_is_filtered_to_the_supplier(clean_db, ingested, embedder):
    with clean_db() as s:
        hits = search(s, "monthly cleaning rate", embedder, doc_type="contract", supplier_id="SUP-002")
    assert hits and {h.supplier_id for h in hits} == {"SUP-002"}


def test_only_the_contract_in_force_on_the_invoice_date_is_used(clean_db, ingested, embedder):
    with clean_db() as s:
        now = search(s, "monthly cleaning rate", embedder, doc_type="contract", supplier_id="SUP-002",
                     as_of=dt.date(2026, 8, 1))  # fmt: skip
        then = search(s, "monthly cleaning rate", embedder, doc_type="contract", supplier_id="SUP-002",
                      as_of=dt.date(2024, 8, 1))  # fmt: skip
    assert {h.contract_id for h in now} == {"MC-2025-004"}
    assert {h.contract_id for h in then} == {"MC-2024-001"}


def test_no_contract_on_file_returns_nothing(clean_db, ingested, embedder):
    with clean_db() as s:
        assert search(s, "advisory fees", embedder, doc_type="contract", supplier_id="SUP-999") == []


def test_keyword_search_finds_exact_policy_ids(clean_db, ingested):
    with clean_db() as s:
        hits = search(s, "POL-004 urgent payment", None, mode="keyword", doc_type="policy", k=1)
    assert "8 Urgent Payment Requests (POL-004)" in hits[0].section


@pytest.mark.parametrize("mode", ["hybrid", "vector", "keyword"])
def test_every_mode_returns_populated_citations(clean_db, ingested, embedder, mode):
    with clean_db() as s:
        hits = search(s, "bank account change verification by telephone", embedder, mode=mode, doc_type="policy")
    assert hits
    for h in hits:
        assert h.citation.source.endswith(".pdf") and h.citation.page and h.citation.section


def test_hybrid_records_both_ranks(clean_db, ingested, embedder):
    with clean_db() as s:
        hits = search(s, "telephone AND email onboarding record verification", embedder, doc_type="policy")
    assert any(h.vector_rank and h.keyword_rank for h in hits)
    assert [h.score for h in hits] == sorted((h.score for h in hits), reverse=True)


# --- context assembly (bug #5) ---------------------------------------------------------------------------


INVOICE = {
    "id": "DOC-1", "invoice_number": "INV-1048", "date": "2026-08-19", "supplier_name": "ABC Office Solutions",
    "line_items": [{"description": "Ergonomic office chairs - Model EX500", "quantity": "25", "unit_price": "4500"}],
}  # fmt: skip
SUPPLIER = {"id": "SUP-001", "name": "ABC Office Solutions"}
RULES = [{"type": "BANK_DETAILS_CHANGED", "description": "Invoice bank account differs from the verified account."}]


def test_every_retrieved_chunk_reaches_the_prompt(clean_db, ingested, embedder):
    with clean_db() as s:
        ctx = rag_context(embedder)(s, INVOICE, SUPPLIER, RULES)
    assert ctx.contract_on_file is True
    assert {i["doc_type"] for i in ctx.items} == {"contract", "policy"} and len(ctx.items) >= 5
    prompt = review_messages(ReviewInput(
        invoice_text="x", rule_findings=RULES, supplier=None, history_count=0, extraction_warnings=[],
        context=ctx.items, line_items=INVOICE["line_items"],
    ))[1].content  # fmt: skip
    for item in ctx.items:
        assert item["text"][-60:] in prompt, f"chunk {item['chunk_id']} did not reach the prompt"
    for item in [i for i in ctx.items if i["doc_type"] == "contract"]:
        assert f"[chunk {item['chunk_id']} |" in prompt


def test_every_item_has_a_citation_and_rules_get_their_policy_section(clean_db, ingested, embedder):
    with clean_db() as s:
        ctx = rag_context(embedder)(s, INVOICE, SUPPLIER, RULES)
    assert all(i["citation"]["source"] and i["citation"]["page"] for i in ctx.items)
    cite = ctx.rule_citations["BANK_DETAILS_CHANGED"][0]
    assert "POL-001" in cite["section"] and cite["page"] == 3


def test_supplier_without_contract_is_said_plainly(clean_db, ingested, embedder):
    with clean_db() as s:
        ctx = rag_context(embedder)(s, INVOICE, {"id": "SUP-999", "name": "Nexus"}, RULES)
    assert ctx.contract_on_file is False and "No contract on file for Nexus" in ctx.notes[0]
    assert all(i["doc_type"] == "policy" for i in ctx.items)


def test_without_ingested_documents_the_policy_table_is_used(clean_db, embedder):
    with clean_db() as s:
        ctx = rag_context(embedder)(s, INVOICE, SUPPLIER, RULES)
    assert ctx.contract_on_file is None and {i["source_id"] for i in ctx.items} == {"POL-001", "POL-002",
                                                                                    "POL-003", "POL-004"}  # fmt: skip
