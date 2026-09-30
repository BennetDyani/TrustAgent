"""Apply one human decision to a case. The only path by which money moves.

Called by the graph's ``execute_action`` node after ``interrupt()`` returns.
Runs in one database transaction with the case row locked (``FOR UPDATE``),
so two approvers acting at once are serialised, and a failure leaves nothing
half-applied.
"""

import datetime as dt
import re
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from trustagent.db import repository as repo
from trustagent.db.models import EvidenceRow, InvestigationRow, InvoiceRow, TransactionRow
from trustagent.domain import Approval, Approver, HumanAction, InvestigationStatus, RiskLevel
from trustagent.workflow.approvals import ROLE_LABELS, ApprovalError, record_approval, separation_of_duties_error
from trustagent.workflow.guards import GuardError, check_action_allowed


@dataclass
class ActionResult:
    ok: bool
    message: str
    closed: bool = False
    status_code: int = 200  # for the API: 403 approval refused, 409 wrong state


def _actor(a: Approver) -> str:
    return f"{a.name} ({ROLE_LABELS[a.role]})"


def bank_details_changed(session: Session, case: InvestigationRow) -> bool:
    return (
        session.scalar(
            select(EvidenceRow.id).where(
                EvidenceRow.investigation_id == case.id,
                EvidenceRow.run_number == case.run_number,
                EvidenceRow.indicator_type == "BANK_DETAILS_CHANGED",
            )
        )
        is not None
    )


def _next_transaction_id(session: Session) -> str:
    ids = session.scalars(select(TransactionRow.id)).all()
    numbers = [int(m.group(1)) for i in ids if (m := re.fullmatch(r"TXN-(\d+)", i))]
    return f"TXN-{max(numbers, default=0) + 1:03d}"


def _request_verification(case: InvestigationRow, actor: Approver, now: dt.datetime) -> None:
    if (case.verification or {}).get("status") == "VERIFIED":
        return
    case.verification = {
        "status": "PENDING",
        "requested_by": _actor(actor),
        "requested_at": now.isoformat(),
        "verified_by": None,
        "verified_at": None,
    }


def apply_human_action(
    session: Session,
    investigation_id: str,
    action: HumanAction,
    actor: Approver,
    note: str | None = None,
    now: dt.datetime | None = None,
) -> ActionResult:
    now = now or dt.datetime.now(dt.UTC)
    case = session.get(InvestigationRow, investigation_id, with_for_update=True)
    if case is None:
        return ActionResult(False, f"Investigation {investigation_id} not found.", status_code=404)
    invoice = session.get(InvoiceRow, case.invoice_id)
    bank_changed = bank_details_changed(session, case)
    suffix = f" Note: {note.strip()}" if note and note.strip() else ""

    try:
        check_action_allowed(InvestigationStatus(case.status), action, case.verification, bank_changed, case.run_number)
    except GuardError as exc:
        repo.append_audit(session, case.id, _actor(actor), f"Action refused: {action}", str(exc), status="FAILED")
        return ActionResult(False, str(exc), status_code=409)

    match action:
        case HumanAction.HOLD_PAYMENT:
            # ADR-030: HOLD keeps the case open, waiting for verification.
            invoice.status = "ON_HOLD"
            _request_verification(case, actor, now)
            detail = f"Held by {_actor(actor)}. Payment for invoice {invoice.invoice_number} is on hold."
            if bank_changed:
                detail += (
                    " The bank account differs from the verified record: finance must confirm the new account by phone"
                    " AND email using contacts from the onboarding records before the supplier is marked verified."
                )
            repo.append_audit(
                session, case.id, _actor(actor), "Payment placed on hold", detail + suffix, "hold_payment"
            )
            result = ActionResult(True, "Payment placed on hold pending verification.")

        case HumanAction.REQUEST_VERIFICATION:
            invoice.status = "UNDER_REVIEW"
            _request_verification(case, actor, now)
            repo.append_audit(session, case.id, _actor(actor), "Verification requested",
                              f"Requested by {_actor(actor)}. Awaiting independent verification of the supplier."
                              + suffix)  # fmt: skip
            result = ActionResult(True, "Verification requested. The case updates once the supplier is verified.")

        case HumanAction.ESCALATE:
            repo.append_audit(session, case.id, _actor(actor), "Case escalated",
                              f"Escalated by {_actor(actor)} for senior review." + suffix)  # fmt: skip
            result = ActionResult(True, "Case escalated for senior review.")

        case HumanAction.REJECT_INVOICE:
            invoice.status = "REJECTED"
            case.status, case.decision = InvestigationStatus.CLOSED.value, HumanAction.REJECT_INVOICE.value
            repo.append_audit(session, case.id, _actor(actor), "Invoice rejected",
                              f"Rejected by {_actor(actor)}; the case is closed and nothing will be paid."
                              + suffix)  # fmt: skip
            result = ActionResult(True, "Invoice rejected and case closed.", closed=True)

        case HumanAction.APPROVE_PAYMENT:
            if sod := separation_of_duties_error(case.verification, actor):
                repo.append_audit(session, case.id, _actor(actor), "Approval refused", sod, status="FAILED")
                return ActionResult(False, sod, status_code=403)
            existing = [Approval.model_validate(a) for a in case.approvals or []]
            level = RiskLevel(case.risk_level) if case.risk_level else None
            outcome = record_approval(invoice.amount, existing, actor, now, risk_level=level)
            if isinstance(outcome, ApprovalError):
                repo.append_audit(session, case.id, _actor(actor), "Approval refused", outcome.error, status="FAILED")
                return ActionResult(False, outcome.error, status_code=403)
            case.approvals = [a.model_dump(mode="json") for a in outcome.approvals]
            if not outcome.complete:
                waiting = " and ".join(ROLE_LABELS[r] for r in outcome.outstanding)
                n, total = len(outcome.approvals), len(outcome.approvals) + len(outcome.outstanding)
                repo.append_audit(session, case.id, _actor(actor), f"Approval {n} of {total} recorded",
                                  f"Approved by {_actor(actor)}. POL-002 dual authorisation: awaiting {waiting}."
                                  + suffix)  # fmt: skip
                result = ActionResult(True, f"Approval recorded. Awaiting {waiting}.")
            else:
                invoice.status = "APPROVED"
                case.status, case.decision = InvestigationStatus.CLOSED.value, HumanAction.APPROVE_PAYMENT.value
                # The approved payment joins the supplier's history for future investigations.
                session.add(TransactionRow(
                    id=_next_transaction_id(session), supplier_id=case.supplier_id, invoice_id=invoice.invoice_number,
                    amount=invoice.amount, currency=invoice.currency, bank_account=invoice.bank_account,
                    date=now.date(), status="COMPLETED",
                    description=(invoice.description or invoice.invoice_number)[:500],
                ))  # fmt: skip
                approvers = " and ".join(_actor(a) for a in outcome.approvals)
                repo.append_audit(session, case.id, _actor(actor), "Payment approved",
                                  f"Approved by {approvers}. Payment for invoice {invoice.invoice_number} released."
                                  + suffix)  # fmt: skip
                result = ActionResult(True, "Payment approved and released.", closed=True)

    session.flush()
    return result
