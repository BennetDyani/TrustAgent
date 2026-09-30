"""POL-002 dual authorisation, ported from the reference approvals.ts."""

import datetime as dt
from decimal import Decimal

import pytest

from trustagent.domain import Approver, Role
from trustagent.workflow.approvals import (
    ApprovalError,
    ApprovalRecorded,
    outstanding_roles,
    record_approval,
    requires_dual_authorization,
)

NOW = dt.datetime(2026, 9, 30, 12, 0, tzinfo=dt.UTC)
ANALYST = Approver(name="Thandi Nkosi", role=Role.FINANCE_ANALYST)
MANAGER = Approver(name="Sipho Dlamini", role=Role.FINANCE_MANAGER)
HEAD = Approver(name="Lerato Khumalo", role=Role.DEPARTMENT_HEAD)
BIG = Decimal("185000")
SMALL = Decimal("18400")


def approve(amount, *approvers):
    approvals, result = [], None
    for approver in approvers:
        result = record_approval(amount, approvals, approver, NOW)
        if isinstance(result, ApprovalRecorded):
            approvals = result.approvals
    return result


@pytest.mark.parametrize("amount, dual", [("100000", False), ("100000.01", True), ("50", False)])
def test_threshold_is_strictly_over_100k(amount, dual):
    assert requires_dual_authorization(Decimal(amount)) is dual


@pytest.mark.parametrize("approver", [ANALYST, MANAGER, HEAD])
def test_small_payment_needs_one_approval_from_any_role(approver):
    result = approve(SMALL, approver)
    assert isinstance(result, ApprovalRecorded) and result.complete


def test_large_payment_needs_manager_and_head():
    first = approve(BIG, MANAGER)
    assert isinstance(first, ApprovalRecorded) and not first.complete
    assert first.outstanding == [Role.DEPARTMENT_HEAD]

    both = approve(BIG, MANAGER, HEAD)
    assert isinstance(both, ApprovalRecorded) and both.complete and both.outstanding == []


def test_order_of_the_two_approvals_does_not_matter():
    result = approve(BIG, HEAD, MANAGER)
    assert isinstance(result, ApprovalRecorded) and result.complete


def test_analyst_cannot_approve_large_payment():
    result = approve(BIG, ANALYST)
    assert isinstance(result, ApprovalError)
    assert "Finance Analyst cannot approve" in result.error


def test_same_role_twice_is_rejected():
    result = approve(BIG, MANAGER, Approver(name="Another Manager", role=Role.FINANCE_MANAGER))
    assert isinstance(result, ApprovalError)
    assert "Department Head" in result.error


def test_same_person_cannot_approve_twice_even_in_a_different_role():
    impostor = Approver(name="  sipho DLAMINI ", role=Role.DEPARTMENT_HEAD)
    result = approve(BIG, MANAGER, impostor)
    assert isinstance(result, ApprovalError)
    assert "different person" in result.error


def test_recorded_approval_carries_timestamp_and_does_not_mutate_input():
    existing: list = []
    result = record_approval(SMALL, existing, MANAGER, NOW)
    assert existing == []
    assert result.approvals[0].approved_at == NOW


def test_outstanding_roles():
    assert outstanding_roles(BIG, []) == [Role.FINANCE_MANAGER, Role.DEPARTMENT_HEAD]
    assert outstanding_roles(SMALL, []) == []  # any single role will do; see is_complete


# --- risk-based authority and separation of duties (weak spot 1, ADR-048) ---------------------

from trustagent.domain import RiskLevel  # noqa: E402
from trustagent.workflow.approvals import separation_of_duties_error  # noqa: E402


@pytest.mark.parametrize("level", [RiskLevel.HIGH, RiskLevel.CRITICAL])
def test_analyst_cannot_approve_high_risk_even_when_small(level):
    result = record_approval(SMALL, [], ANALYST, NOW, risk_level=level)
    assert isinstance(result, ApprovalError) and "Finance Manager or a Department Head" in result.error


@pytest.mark.parametrize("approver", [MANAGER, HEAD])
def test_manager_or_head_can_approve_high_risk_small_payment(approver):
    result = record_approval(SMALL, [], approver, NOW, risk_level=RiskLevel.CRITICAL)
    assert isinstance(result, ApprovalRecorded) and result.complete


@pytest.mark.parametrize("level", [RiskLevel.LOW, RiskLevel.MEDIUM, None])
def test_analyst_can_still_approve_low_and_medium(level):
    assert isinstance(record_approval(SMALL, [], ANALYST, NOW, risk_level=level), ApprovalRecorded)


def test_whoever_verified_the_supplier_cannot_approve():
    verification = {"status": "VERIFIED", "verified_by_name": "Sipho Dlamini"}
    assert "separation of duties" in separation_of_duties_error(verification, MANAGER)
    assert separation_of_duties_error(verification, HEAD) is None
    assert separation_of_duties_error(None, MANAGER) is None
    assert separation_of_duties_error({"status": "PENDING"}, MANAGER) is None
