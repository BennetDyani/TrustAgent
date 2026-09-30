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
