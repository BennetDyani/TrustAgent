"""The single-call LLM steps: AI review and report writing.

Each takes plain inputs and returns a typed result plus token usage, and
raises ``LLMUnavailable`` on any failure. The graph nodes turn that into the
rule-only fallback. Tests replace these callables with fakes.
"""

import re
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Literal

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import BaseModel, Field

from trustagent.graph.prompts import AI_REVIEW_SYSTEM, DOC_TAG, REPORT_SYSTEM
from trustagent.llm.factory import get_chat_model
from trustagent.llm.usage import invoke_structured

Severity = Literal["LOW", "MEDIUM", "HIGH", "CRITICAL"]
RuleType = Literal[
    "BANK_DETAILS_CHANGED", "DUPLICATE_INVOICE", "ACCOUNT_HOLDER_MISMATCH", "EMAIL_DOMAIN_MISMATCH", "UNUSUAL_AMOUNT",
    "PERSONAL_EMAIL_DOMAIN", "SUPPLIER_NOT_VERIFIED", "AMOUNT_EXCEEDS_THRESHOLD", "THRESHOLD_AVOIDANCE",
    "PATTERN_ANOMALY", "URGENCY_INDICATOR", "MULTIPLE_BANK_ACCOUNTS", "SHARED_BANK_ACCOUNT", "CONTRACT_DEVIATION",
]  # fmt: skip


class AIObservation(BaseModel):
    # The schema already restricts the type; code filters again (defence in depth, ADR-002).
    type: Literal["SOCIAL_ENGINEERING", "DOCUMENT_ANOMALY", "OTHER"]
    description: str = Field(description="What was observed and why it matters, in one or two sentences.")
    severity: Severity
    quote: str | None = Field(
        description="The exact words copied from the invoice that this is based on. Checked by code: an observation "
        "whose quote is not in the invoice is discarded."
    )
    relates_to_rule: RuleType | None = Field(
        description="If this is about the same concern as one of the RULE FINDINGS, that rule's type; otherwise "
        "null. Overlapping observations are kept as evidence but add no points."
    )


class ContractTerm(BaseModel):
    """One invoice line mapped to the contract rate that covers it. Code checks and compares (ADR-049)."""

    invoice_line: str = Field(description="The invoice line item description, exactly as on the invoice.")
    contract_item: str = Field(description="The matching item in the contract rate table, as printed.")
    agreed_price: str = Field(
        description="The contract price exactly as printed, e.g. 'R 3,950.00' or '10% of the order value'."
    )
    price_basis: Literal["per_unit", "percentage", "other"] = Field(
        description="per_unit for a price per item/month/occasion; percentage for a % surcharge; other otherwise."
    )
    source_chunk_id: int = Field(description="The [chunk N] id of the contract clause you took the price from.")
    quote: str = Field(description="The exact contract text showing the item and its price, copied from that chunk.")


class AIReview(BaseModel):
    observations: list[AIObservation]
    contract_terms: list[ContractTerm] = Field(
        description="For each invoice line covered by the CONTRACT CLAUSES, the agreed rate. Empty if no contract."
    )
    summary: str = Field(description="Two or three sentences: your overall reading of the document.")


class ReportDraft(BaseModel):
    summary: str
    recommendation: str
    recommended_action: Literal["APPROVE_PAYMENT", "REQUEST_VERIFICATION", "ESCALATE", "HOLD_PAYMENT"]


@dataclass
class ReviewInput:
    invoice_text: str
    rule_findings: list[dict[str, Any]]
    supplier: dict[str, Any] | None
    history_count: int
    extraction_warnings: list[str]
    context: list[dict[str, Any]]
    line_items: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class ReportInput:
    invoice: dict[str, Any]
    score: int
    level: str
    minimum_action: str
    evidence: list[dict[str, Any]]
    ai_review_available: bool
    ai_summary: str
    deep_dive_summary: str | None
    next_steps: list[str]


Reviewer = Callable[[ReviewInput], tuple[AIReview, dict[str, Any]]]
Reporter = Callable[[ReportInput], tuple[ReportDraft, dict[str, Any]]]


def _neutralise(text: str) -> str:
    return re.sub(rf"</?\s*{DOC_TAG}\s*>", "[removed tag]", text, flags=re.IGNORECASE)


def _bullets(lines: list[str]) -> str:
    return "\n".join(f"- {line}" for line in lines) if lines else "- none"


NO_CONTRACT = "NO CONTRACT ON FILE: return an empty contract_terms list; do not guess rates."


def review_messages(inp: ReviewInput) -> list:
    flagged = [f for f in inp.rule_findings if f["type"] != "CONFIRMED_MATCH"]
    confirmed = [f for f in inp.rule_findings if f["type"] == "CONFIRMED_MATCH"]
    sup = inp.supplier
    supplier_line = (
        f"{sup['name']} ({sup['id']}), verified={sup['verified']}, completed payments on file={inp.history_count}"
        if sup
        else "No supplier record."
    )
    contract = [
        f"[chunk {c['chunk_id']} | {c['source_id']} | {c['title']}]\n{c['text']}"
        for c in inp.context
        if c["doc_type"] == "contract"
    ]
    policies = [f"[{c['source_id']} | {c['title']}] {c['text']}" for c in inp.context if c["doc_type"] == "policy"]
    lines = [f"{li['description']} | qty {li['quantity']} | unit R{li['unit_price']}" for li in inp.line_items]
    body = f"""RULE FINDINGS (established facts, already scored):
{_bullets([f"{f['type']}: {f['description']}" for f in flagged])}

CONFIRMED CHECKS:
{_bullets([f["description"] for f in confirmed])}

SUPPLIER RECORD: {supplier_line}

EXTRACTION WARNINGS (from code validation):
{_bullets(inp.extraction_warnings)}

RELEVANT POLICIES:
{_bullets(policies)}

INVOICE LINE ITEMS (as extracted):
{_bullets(lines)}

CONTRACT CLAUSES (this supplier's agreement in force on the invoice date):
{chr(10).join(contract) if contract else NO_CONTRACT}

<{DOC_TAG}>
{_neutralise(inp.invoice_text)}
</{DOC_TAG}>"""
    return [SystemMessage(AI_REVIEW_SYSTEM), HumanMessage(body)]


def report_messages(inp: ReportInput) -> list:
    inv = inp.invoice

    def cites(e: dict) -> str:
        c = [f"{x['source']}, {x.get('section') or ''}, p.{x.get('page')}" for x in e.get("citations") or []]
        return f" (source: {'; '.join(c)})" if c else ""

    evidence = [
        f"{e['type']} [{e['source']}, weight {e['weight']}]: {e['description']}{cites(e)}"
        for e in inp.evidence
        if e["type"] != "CONFIRMED_MATCH"
    ]
    confirmed = [e["description"] for e in inp.evidence if e["type"] == "CONFIRMED_MATCH"]
    headline = f"{inv['invoice_number']} from {inv['supplier_name']}, R{float(inv['amount']):,.2f}"
    body = f"""INVOICE: {headline}, due {inv.get("due_date")}

RISK (decided by code): score {inp.score}/100, level {inp.level}, MINIMUM action {inp.minimum_action}

EVIDENCE:
{_bullets(evidence)}

CHECKS THAT PASSED:
{_bullets(confirmed)}

REQUIRED NEXT STEPS (decided by code; use only these):
{_bullets(inp.next_steps)}

AI DOCUMENT REVIEW: {inp.ai_summary if inp.ai_review_available else "UNAVAILABLE: assessment is rule-based."}
DEEP DIVE: {inp.deep_dive_summary or "not run"}"""
    return [SystemMessage(REPORT_SYSTEM), HumanMessage(body)]


def llm_reviewer(llm: BaseChatModel | None = None) -> Reviewer:
    def review(inp: ReviewInput) -> tuple[AIReview, dict[str, Any]]:
        return invoke_structured(llm or get_chat_model("generator"), AIReview, review_messages(inp), "ai_review")

    return review


def llm_reporter(llm: BaseChatModel | None = None) -> Reporter:
    def report(inp: ReportInput) -> tuple[ReportDraft, dict[str, Any]]:
        return invoke_structured(llm or get_chat_model("generator"), ReportDraft, report_messages(inp), "report")

    return report
