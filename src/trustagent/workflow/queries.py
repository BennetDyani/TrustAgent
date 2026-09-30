"""Read models for the API and UI: case list, case detail, suppliers.

"What may this person do right now, and why not?" is answered with the SAME
pure guard and approval functions the server enforces on every action. The
UI can show a disabled Approve button with its reason, but the server still
checks again when the action arrives.
"""

import datetime as dt
from decimal import Decimal
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from trustagent.db.models import AuditLogRow, EvidenceRow, InvestigationRow, InvoiceRow, SupplierRow
from trustagent.domain import Approval, Approver, HumanAction, InvestigationStatus, RiskLevel
from trustagent.workflow.actions import bank_details_changed
from trustagent.workflow.approvals import ApprovalError, record_approval, separation_of_duties_error
from trustagent.workflow.guards import GuardError, check_action_allowed


def _case_row(case: InvestigationRow, invoice: InvoiceRow) -> dict[str, Any]:
    return {
        "id": case.id,
        "status": case.status,
        "run_number": case.run_number,
        "risk_score": case.risk_score,
        "risk_level": case.risk_level,
        "recommended_action": case.recommended_action,
        "decision": case.decision,
        "invoice_number": invoice.invoice_number,
        "supplier_id": case.supplier_id,
        "supplier_name": invoice.supplier_name,
        "amount": invoice.amount,
        "invoice_status": invoice.status,
        "verification": (case.verification or {}).get("status"),
        "created_at": case.created_at,
    }


def list_cases(session: Session) -> dict[str, Any]:
    rows = session.execute(
        select(InvestigationRow, InvoiceRow)
        .join(InvoiceRow, InvoiceRow.id == InvestigationRow.invoice_id)
        .order_by(InvestigationRow.created_at.desc(), InvestigationRow.id)
    ).all()
    cases = [_case_row(c, i) for c, i in rows]
    return {
        "cases": cases,
        "metrics": {
            "total": len(cases),
            "high_risk": sum(c["risk_level"] in ("HIGH", "CRITICAL") for c in cases),
            "action_required": sum(c["status"] == "ACTION_REQUIRED" for c in cases),
            "on_hold": sum(c["invoice_status"] == "ON_HOLD" for c in cases),
        },
    }


def available_actions(session: Session, case: InvestigationRow, invoice: InvoiceRow, viewer: Approver) -> list[dict]:
    """Each human action with ``allowed`` and, if not, the reason (a dry run of the real checks)."""
    if case.status != InvestigationStatus.ACTION_REQUIRED:
        return []  # evidence before decisions: no options until the investigation has completed
    bank_changed = bank_details_changed(session, case)
    out = []
    for action in HumanAction:
        reason = None
        try:
            check_action_allowed(
                InvestigationStatus(case.status), action, case.verification, bank_changed, case.run_number
            )
            if action == HumanAction.APPROVE_PAYMENT:
                reason = separation_of_duties_error(case.verification, viewer)
                if reason is None:
                    existing = [Approval.model_validate(a) for a in case.approvals or []]
                    level = RiskLevel(case.risk_level) if case.risk_level else None
                    outcome = record_approval(invoice.amount, existing, viewer, dt.datetime.now(dt.UTC), level)
                    reason = outcome.error if isinstance(outcome, ApprovalError) else None
        except GuardError as exc:
            reason = str(exc)
        out.append({"action": action.value, "allowed": reason is None, "reason": reason})
    return out


def case_detail(session: Session, investigation_id: str, viewer: Approver) -> dict[str, Any] | None:
    case = session.get(InvestigationRow, investigation_id)
    if case is None:
        return None
    invoice = session.get(InvoiceRow, case.invoice_id)
    supplier = session.get(SupplierRow, case.supplier_id) if case.supplier_id else None
    evidence = session.scalars(
        select(EvidenceRow)
        .where(EvidenceRow.investigation_id == case.id, EvidenceRow.run_number == case.run_number)
        .order_by(EvidenceRow.weight.desc(), EvidenceRow.id)
    ).all()
    audit = session.scalars(
        select(AuditLogRow).where(AuditLogRow.investigation_id == case.id).order_by(AuditLogRow.id)
    ).all()
    usage = case.llm_usage or []
    return {
        **_case_row(case, invoice),
        "summary": case.summary,
        "recommendation": case.recommendation,
        "ai_review_available": case.ai_review_available,
        "deep_dive_summary": case.deep_dive_summary,
        "approvals": case.approvals or [],
        "verification_detail": case.verification,
        "invoice": {
            "invoice_number": invoice.invoice_number,
            "supplier_name": invoice.supplier_name,
            "amount": invoice.amount,
            "currency": invoice.currency,
            "date": invoice.date,
            "due_date": invoice.due_date,
            "bank_account": invoice.bank_account,  # masked
            "bank_name": invoice.bank_name,
            "bank_account_holder": invoice.bank_account_holder,
            "supplier_email": invoice.supplier_email,
            "urgency": invoice.urgency,
            "line_items": invoice.line_items,
            "document_accounts": invoice.document_accounts,
            "extraction_warnings": invoice.extraction_warnings,
            "source_filename": invoice.source_filename,
        },
        "supplier": _supplier(supplier) if supplier else None,
        "evidence": [
            {
                "type": e.indicator_type,
                "source": e.source,
                "severity": e.severity,
                "weight": e.weight,
                "description": e.description,
                "citations": e.citations,
            }  # fmt: skip
            for e in evidence
        ],
        "audit": [
            {"timestamp": a.timestamp, "actor": a.actor, "action": a.action, "detail": a.detail, "status": a.status}
            for a in audit
        ],
        "tokens": sum(u["input_tokens"] + u["output_tokens"] for u in usage),
        "actions": available_actions(session, case, invoice, viewer),
    }


def _supplier(s: SupplierRow) -> dict[str, Any]:
    return {
        "id": s.id,
        "name": s.name,
        "contact_email": s.contact_email,
        "bank_account": s.bank_account,
        "bank_name": s.bank_name,
        "verified": s.verified,
        "verified_by": s.verified_by,
        "verified_date": s.verified_date,
        "risk_status": s.risk_status,
        "expected_spend_min": s.expected_spend_min,
        "expected_spend_max": s.expected_spend_max,
    }


def list_suppliers(session: Session) -> list[dict[str, Any]]:
    open_cases = dict(
        session.execute(
            select(InvestigationRow.supplier_id, func.count())
            .where(InvestigationRow.status == "ACTION_REQUIRED")
            .group_by(InvestigationRow.supplier_id)
        ).all()
    )
    return [
        {**_supplier(s), "open_cases": open_cases.get(s.id, 0)}
        for s in session.scalars(select(SupplierRow).order_by(SupplierRow.id))
    ]


def json_default(value: Any) -> Any:
    """For SSE / JSON: Decimals as strings (exact money), dates as ISO."""
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, dt.date | dt.datetime):
        return value.isoformat()
    raise TypeError(f"Not JSON serialisable: {type(value).__name__}")
