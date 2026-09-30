"""Assemble retrieved context for an investigation.

- Contract clauses: hybrid search over THIS supplier's agreement in force on the invoice date,
  queried with the invoice's line items. No agreement found means "no contract on file", never a guess.
- Policy sections: hybrid search queried with the rule findings.
- Rule citations: each fired rule gets the policy-manual section that governs it, found by
  keyword search (no model call), so evidence says *which* rule of the manual applies.

Every retrieved chunk is kept (bug #5: an indentation slip once sent only the last chunk to the
prompt), and each carries a populated citation.
"""

import datetime as dt
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from trustagent.db.models import DocumentChunkRow, PolicyRow
from trustagent.rag.embeddings import Embedder
from trustagent.rag.retrieve import RetrievedChunk, search

# Which part of the policy manual governs each rule finding (keyword query, no embedding needed).
RULE_POLICY_QUERY = {
    "BANK_DETAILS_CHANGED": "POL-001 bank account changes verification procedure",
    "SUPPLIER_NOT_VERIFIED": "supplier onboarding master data verification independent sources",
    "ACCOUNT_HOLDER_MISMATCH": "name on the bank account must match the supplier legal name",
    "EMAIL_DOMAIN_MISMATCH": "never use email address supplied in the change request",
    "PERSONAL_EMAIL_DOMAIN": "contact details printed on invoice not an independent source",
    "AMOUNT_EXCEEDS_THRESHOLD": "POL-002 payment authorisation thresholds dual authorisation",
    "THRESHOLD_AVOIDANCE": "splitting purchase below threshold prohibited escalate",
    "UNUSUAL_AMOUNT": "POL-003 expected spending range flagged",
    "PATTERN_ANOMALY": "POL-003 three times historical average",
    "URGENCY_INDICATOR": "POL-004 urgent payment requests additional scrutiny",
    "DUPLICATE_INVOICE": "duplicate invoices paid once re-issued",
    "MULTIPLE_BANK_ACCOUNTS": "nominated bank account verification procedure",
    "SHARED_BANK_ACCOUNT": "bank account verification fraud response same bank account",
}


@dataclass
class RetrievedContext:
    items: list[dict[str, Any]]
    contract_on_file: bool | None  # None = not applicable (no supplier) or retrieval not available
    rule_citations: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)


ContextRetriever = Callable[[Session, dict, dict | None, list[dict]], RetrievedContext]


def _item(c: RetrievedChunk) -> dict[str, Any]:
    return {
        "chunk_id": c.id,
        "doc_type": c.doc_type,
        "source_id": c.contract_id or c.document_id,
        "contract_id": c.contract_id,
        "title": c.section or c.source,
        "text": c.content,
        "citation": c.citation.model_dump(),
    }


def policy_table_context(session: Session, invoice: dict, supplier: dict | None, rules: list[dict]) -> RetrievedContext:
    """Fallback when no documents have been ingested: the four policies from the policy table."""
    items = [
        {
            "chunk_id": None,
            "doc_type": "policy",
            "source_id": p.id,
            "contract_id": None,
            "title": p.name,
            "text": p.rule,
            "citation": {"source": p.id, "page": None, "section": p.name},
        }  # fmt: skip
        for p in session.scalars(select(PolicyRow).order_by(PolicyRow.id))
    ]
    return RetrievedContext(items, None, notes=["Documents not ingested: using the policy table only."])


def rag_context(embedder: Embedder) -> ContextRetriever:
    def retrieve(session: Session, invoice: dict, supplier: dict | None, rules: list[dict]) -> RetrievedContext:
        if not session.scalar(select(func.count()).select_from(DocumentChunkRow)):
            return policy_table_context(session, invoice, supplier, rules)

        items: list[dict[str, Any]] = []
        notes: list[str] = []
        contract_on_file = None
        if supplier:
            as_of = dt.date.fromisoformat(invoice["date"]) if invoice.get("date") else dt.date.today()
            lines = "; ".join(li["description"] for li in invoice.get("line_items", []))
            clauses = search(session, f"Agreed contract rates and prices for: {lines}", embedder,
                             k=4, doc_type="contract", supplier_id=supplier["id"], as_of=as_of)  # fmt: skip
            contract_on_file = bool(clauses)
            if not clauses:
                notes.append(f"No contract on file for {supplier['name']} in force on {as_of}; prices not checked.")
            items += [_item(c) for c in clauses]

        flagged = [r for r in rules if r["type"] != "CONFIRMED_MATCH"]
        policy_query = "; ".join(r["description"] for r in flagged) or "supplier payment approval and verification"
        items += [_item(c) for c in search(session, policy_query, embedder, k=4, doc_type="policy")]

        rule_citations: dict[str, list[dict[str, Any]]] = {}
        for rule_type in {r["type"] for r in flagged}:
            if query := RULE_POLICY_QUERY.get(rule_type):
                hits = search(session, query, None, k=1, mode="keyword", doc_type="policy")
                if hits:
                    rule_citations[rule_type] = [hits[0].citation.model_dump()]
        return RetrievedContext(items, contract_on_file, rule_citations, notes)

    return retrieve
