"""POL-002 dual authorisation, as pure functions.

Payments over R100,000 need a Finance Manager AND a Department Head, who must
be two different people. A Finance Analyst can't approve them. At or below
R100,000, one approval from any finance role is enough.

Who the approver is comes from the server-side identity ("acting as"), never
from the request body (bug #8). This module only checks what that person may do.
"""

import datetime as dt
from dataclasses import dataclass
from decimal import Decimal

from trustagent.config import get_settings
from trustagent.domain import Approval, Approver, Role

DUAL_AUTHORIZATION_ROLES: tuple[Role, ...] = (Role.FINANCE_MANAGER, Role.DEPARTMENT_HEAD)

ROLE_LABELS = {
    Role.FINANCE_ANALYST: "Finance Analyst",
    Role.FINANCE_MANAGER: "Finance Manager",
    Role.DEPARTMENT_HEAD: "Department Head",
}


@dataclass(frozen=True)
class ApprovalRecorded:
    approvals: list[Approval]
    complete: bool
    outstanding: list[Role]


@dataclass(frozen=True)
class ApprovalError:
    error: str


def _threshold() -> Decimal:
    return get_settings().large_transaction_threshold


def requires_dual_authorization(amount: Decimal) -> bool:
    return amount > _threshold()


def outstanding_roles(amount: Decimal, approvals: list[Approval]) -> list[Role]:
    """Roles still required before a dual-authorisation payment is complete.

    Empty for single-approval payments: there any one finance role will do.
    """
    if not requires_dual_authorization(amount):
        return []
    have = {a.role for a in approvals}
    return [role for role in DUAL_AUTHORIZATION_ROLES if role not in have]


def is_complete(amount: Decimal, approvals: list[Approval]) -> bool:
    if requires_dual_authorization(amount):
        return not outstanding_roles(amount, approvals)
    return len(approvals) >= 1


def _same_person(a: str, b: str) -> bool:
    return a.strip().casefold() == b.strip().casefold()


def record_approval(
    amount: Decimal, existing: list[Approval], approver: Approver, now: dt.datetime
) -> ApprovalRecorded | ApprovalError:
    """Validate one approval against POL-002 and return the new approval list (input is not mutated)."""
    if any(_same_person(a.name, approver.name) for a in existing):
        return ApprovalError(
            f"{approver.name} has already approved this payment. A second approval must come from a different person."
        )

    if requires_dual_authorization(amount):
        if approver.role not in DUAL_AUTHORIZATION_ROLES:
            return ApprovalError(
                f"Payments over R{_threshold():,.0f} need approval from a Finance Manager and a Department Head "
                f"(POL-002). A {ROLE_LABELS[approver.role]} cannot approve this payment."
            )
        if any(a.role == approver.role for a in existing):
            needed = outstanding_roles(amount, existing)
            return ApprovalError(
                f"A {ROLE_LABELS[approver.role]} has already approved this payment. The second approval must "
                f"come from a {ROLE_LABELS[needed[0]]}."
            )

    approvals = [*existing, Approval(name=approver.name, role=approver.role, approved_at=now)]
    return ApprovalRecorded(
        approvals=approvals,
        complete=is_complete(amount, approvals),
        outstanding=outstanding_roles(amount, approvals),
    )
