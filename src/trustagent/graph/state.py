"""The investigation graph's state.

Only JSON-safe values (dicts, lists, strings, numbers) are stored, never
Pydantic objects. Checkpoints then don't depend on how Python classes
serialise, and a run paused for days resumes after a code deploy.

Fields with ``operator.add`` accumulate across nodes; the rest are replaced.
"""

import operator
from typing import Annotated, Any, TypedDict


class InvestigationState(TypedDict, total=False):
    # Identity of the run
    investigation_id: str
    run_number: int

    # Facts loaded from the database (the invoice was extracted at intake)
    invoice: dict[str, Any]
    supplier: dict[str, Any] | None
    supplier_verified: bool
    history_count: int
    invoice_text: str  # redacted document text: untrusted input for the AI review

    # Findings, each a RiskIndicator as a dict, tagged with its source
    rule_findings: list[dict[str, Any]]
    ai_findings: list[dict[str, Any]]
    contract_findings: list[dict[str, Any]]
    deep_dive_findings: list[dict[str, Any]]
    ignored_ai_types: Annotated[list[str], operator.add]

    # Retrieved context (policies now; contracts via RAG in phase 4)
    context: list[dict[str, Any]]

    ai_review_available: bool
    ai_summary: str

    provisional: dict[str, Any]  # {score, level}
    deep_dive: dict[str, Any] | None  # {ran, reason, summary, completed}
    risk: dict[str, Any]  # {score, level, indicators}
    report: dict[str, Any]  # {summary, recommendation, recommended_action, raised, fallback}

    # Human-in-the-loop
    pending_action: dict[str, Any] | None  # {action, actor, note} from the resume payload
    last_action_result: dict[str, Any] | None  # {ok, message, closed}
    closed: bool

    usage: Annotated[list[dict[str, Any]], operator.add]
    notes: Annotated[list[str], operator.add]
