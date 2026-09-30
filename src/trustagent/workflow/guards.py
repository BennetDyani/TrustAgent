"""State guards: which transitions are allowed from which status.

These are the pure rules. The database layer enforces the start of a run
atomically with a conditional UPDATE (``... WHERE status = 'PENDING'``), so
two clicks can't start two runs (bug #12); these functions give the clear error.
"""

from typing import Any

from trustagent.domain import Action, InvestigationStatus


class GuardError(Exception):
    """An action isn't allowed in the investigation's current state (HTTP 409)."""


def check_can_start(status: InvestigationStatus) -> None:
    if status != InvestigationStatus.PENDING:
        raise GuardError(f"An investigation can only start from PENDING; this one is {status}.")


def check_action_allowed(
    status: InvestigationStatus,
    action: Action,
    verification: dict[str, Any] | None,
    bank_details_changed: bool = False,
) -> None:
    if status != InvestigationStatus.ACTION_REQUIRED:
        raise GuardError(f"Actions can only be taken on ACTION_REQUIRED cases; this one is {status}.")
    if action != Action.APPROVE_PAYMENT:
        return
    verification_status = (verification or {}).get("status")
    if bank_details_changed and verification_status != "VERIFIED":
        # ADR-029: a changed bank account can't be paid until finance has verified it.
        raise GuardError(
            "The bank account on this invoice differs from the verified record. Payment stays on hold until the "
            "finance team verifies the new account by phone and email and marks the supplier as verified."
        )
    if verification_status == "PENDING":
        raise GuardError("Supplier verification is still pending. Verify the supplier before approving payment.")


def check_can_rerun(status: InvestigationStatus) -> None:
    """Re-runs are for open cases (e.g. after supplier verification) and runs that failed."""
    if status not in (InvestigationStatus.ACTION_REQUIRED, InvestigationStatus.FAILED):
        raise GuardError(f"Only open or failed investigations can be re-run; this one is {status}.")
