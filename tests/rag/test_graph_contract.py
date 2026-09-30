"""End to end: RAG context -> AI maps lines to contract rates -> code finds the deviation -> cited evidence."""

import pytest

from tests.graph.conftest import USAGE, FakeLLM
from trustagent.config import get_settings
from trustagent.extraction.schema import ExtractedLineItem
from trustagent.graph.builder import build_graph
from trustagent.graph.llm_steps import AIObservation, AIReview, ContractTerm
from trustagent.graph.nodes import Deps
from trustagent.rag.context import rag_context
from trustagent.workflow.identity import DEMO_USERS
from trustagent.workflow.service import InvestigationService


class ContractAwareFake(FakeLLM):
    """Maps the chairs line to whichever retrieved chunk holds the rate, like the real model is asked to."""

    def reviewer(self, inp):
        self.review_inputs.append(inp)
        rate = next(c for c in inp.context if c["doc_type"] == "contract" and "R 3,950.00" in c["text"])
        terms = [ContractTerm(
            invoice_line="Ergonomic office chairs - Model EX500", contract_item="Ergonomic office chairs - Model EX500",
            agreed_price="R 3,950.00", price_basis="per_unit", source_chunk_id=rate["chunk_id"],
            quote="Ergonomic office chairs - Model EX500 each R 3,950.00",
        )]  # fmt: skip
        restated = AIObservation(type="DOCUMENT_ANOMALY", severity="MEDIUM", description="Chairs priced high.",
                                 quote="Ergonomic office chairs", relates_to_rule="CONTRACT_DEVIATION")  # fmt: skip
        return AIReview(observations=[restated], contract_terms=terms, summary="Fake."), USAGE


@pytest.fixture
def rag_service(clean_db, ingested, embedder):
    fake = ContractAwareFake()
    deps = Deps(session_factory=clean_db, reviewer=fake.reviewer, reporter=fake.reporter,
                deep_diver=fake.deep_diver, context_retriever=rag_context(embedder))  # fmt: skip
    svc = InvestigationService.create(get_settings().test_database_url, deps=deps)
    yield svc, fake
    svc.close()


def test_contract_deviation_is_found_cited_and_scored(rag_service, upload, clean_db):
    service, fake = rag_service
    # ABC Office Solutions, verified account ****4821, invoice date 2026-08-01 (contract ABC-2026-017 in force).
    case_id = upload(supplier_name="ABC Office Solutions", bank_account_number="62718304554821",
                     supplier_email="accounts@abcoffice.co.za", bank_account_holder="ABC Office Solutions",
                     total_due="R 112,500.00", subtotal=None, vat_amount=None,
                     line_items=[ExtractedLineItem(
                         description="Ergonomic office chairs - Model EX500", quantity="25",
                         unit_price="R 4,500.00", total="R 112,500.00")])  # fmt: skip
    events = list(service.start(case_id, DEMO_USERS["thandi"]))
    evidence = [e for e in events if e["event"] == "evidence"]

    dev = next(e for e in evidence if e["type"] == "CONTRACT_DEVIATION")
    assert dev["source"] == "CONTRACT" and dev["weight"] == 20
    assert dev["citations"][0]["source"].startswith("ABC-2026-017") and dev["citations"][0]["section"] == "4 Rates"

    restated = next(e for e in evidence if e["source"] == "AI")
    assert restated["weight"] == 0  # overlaps a code finding: evidence only (ADR-042)

    unusual = next(e for e in evidence if e["type"] == "UNUSUAL_AMOUNT")
    assert unusual["citations"] and "POL-003" in unusual["citations"][0]["section"]

    assert "Query the price with the supplier" in " ".join(fake.report_inputs[0].next_steps)
    report_sources = [c["source"] for e in fake.report_inputs[0].evidence for c in e.get("citations", [])]
    assert any(src.startswith("ABC-2026-017") for src in report_sources)  # the report writer sees the citation


def test_unknown_supplier_has_no_contract_and_no_contract_findings(rag_service, upload):
    service, fake = rag_service
    fake_reviewer = FakeLLM()
    service.deps.reviewer = fake_reviewer.reviewer
    service.graph = build_graph(service.deps, service.checkpointer)
    case_id = upload(supplier_name="Nexus Advisory Partners", supplier_email="a@nexus.co.za",
                     bank_account_holder="Nexus Advisory Partners")  # fmt: skip
    events = list(service.start(case_id, DEMO_USERS["thandi"]))
    retrieved = next(e for e in events if e.get("action") == "Policies and contracts retrieved")
    assert "no contract on file" in retrieved["detail"]
    assert not [e for e in events if e["event"] == "evidence" and e["source"] == "CONTRACT"]
    assert all(c["doc_type"] == "policy" for c in fake_reviewer.review_inputs[0].context)
