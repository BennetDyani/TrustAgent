"""State guards: which transitions are allowed from which status (bug #12)."""

import pytest

from trustagent.domain import Action
from trustagent.domain import InvestigationStatus as S
from trustagent.workflow.guards import GuardError, check_action_allowed, check_can_rerun, check_can_start


def test_start_only_from_pending():
    check_can_start(S.PENDING)
    for status in (S.IN_PROGRESS, S.ACTION_REQUIRED, S.CLOSED, S.FAILED):
        with pytest.raises(GuardError):
            check_can_start(status)


@pytest.mark.parametrize("action", list(Action))
def test_actions_only_on_action_required(action):
    check_action_allowed(S.ACTION_REQUIRED, action, verification=None)
    for status in (S.PENDING, S.IN_PROGRESS, S.CLOSED, S.FAILED):
        with pytest.raises(GuardError):
            check_action_allowed(status, action, verification=None)


def test_approve_blocked_while_verification_pending():
    with pytest.raises(GuardError, match="verification is still pending"):
        check_action_allowed(S.ACTION_REQUIRED, Action.APPROVE_PAYMENT, verification={"status": "PENDING"})


def test_approve_allowed_once_verified():
    check_action_allowed(S.ACTION_REQUIRED, Action.APPROVE_PAYMENT, verification={"status": "VERIFIED"})


@pytest.mark.parametrize("action", [Action.HOLD_PAYMENT, Action.ESCALATE])
def test_hold_and_escalate_allowed_while_verification_pending(action):
    check_action_allowed(S.ACTION_REQUIRED, action, verification={"status": "PENDING"})


def test_rerun_only_on_open_cases():
    check_can_rerun(S.ACTION_REQUIRED)
    check_can_rerun(S.FAILED)  # a run that crashed can be retried
    for status in (S.PENDING, S.IN_PROGRESS, S.CLOSED):
        with pytest.raises(GuardError):
            check_can_rerun(status)


# --- bank-details change: no approval until verified by phone and email (ADR-029) ----------


def test_bank_change_blocks_approval_until_verified():
    with pytest.raises(GuardError, match="phone and email"):
        check_action_allowed(S.ACTION_REQUIRED, Action.APPROVE_PAYMENT, verification=None, bank_details_changed=True)
    with pytest.raises(GuardError):
        check_action_allowed(
            S.ACTION_REQUIRED, Action.APPROVE_PAYMENT, verification={"status": "PENDING"}, bank_details_changed=True
        )
    check_action_allowed(
        S.ACTION_REQUIRED, Action.APPROVE_PAYMENT, verification={"status": "VERIFIED"}, bank_details_changed=True
    )


@pytest.mark.parametrize("action", [Action.HOLD_PAYMENT, Action.ESCALATE, Action.REQUEST_VERIFICATION])
def test_bank_change_still_allows_the_cautious_actions(action):
    check_action_allowed(S.ACTION_REQUIRED, action, verification=None, bank_details_changed=True)


# --- re-run required after verification (weak spot 1, ADR-049) --------------------------------


def test_approval_after_verification_needs_a_rerun_first():
    verified = {"status": "VERIFIED", "verified_during_run": 1}
    with pytest.raises(GuardError, match="Re-run"):
        check_action_allowed(S.ACTION_REQUIRED, Action.APPROVE_PAYMENT, verified, run_number=1)
    check_action_allowed(S.ACTION_REQUIRED, Action.APPROVE_PAYMENT, verified, run_number=2)


def test_rerun_rule_does_not_block_cautious_actions():
    verified = {"status": "VERIFIED", "verified_during_run": 1}
    check_action_allowed(S.ACTION_REQUIRED, Action.HOLD_PAYMENT, verified, run_number=1)
