import pytest

from trustagent.domain import Action, IndicatorSource, RiskLevel, Severity
from trustagent.rules.recommendation import (
    AI_INDICATOR_TYPES,
    fallback_recommendation,
    filter_ai_indicators,
    minimum_action,
    resolve_recommended_action,
)

A = Action


@pytest.mark.parametrize(
    "level, verified, expected",
    [
        (RiskLevel.CRITICAL, True, A.HOLD_PAYMENT),
        (RiskLevel.HIGH, True, A.HOLD_PAYMENT),
        (RiskLevel.MEDIUM, True, A.REQUEST_VERIFICATION),
        (RiskLevel.LOW, True, A.APPROVE_PAYMENT),
        (RiskLevel.LOW, False, A.REQUEST_VERIFICATION),
        (None, True, A.ESCALATE),
        (None, False, A.ESCALATE),
    ],
)
def test_minimum_action_table(level, verified, expected):
    assert minimum_action(level, verified) == expected


@pytest.mark.parametrize("proposed", [A.HOLD_PAYMENT, A.ESCALATE, A.REQUEST_VERIFICATION])
def test_more_cautious_proposal_is_accepted(proposed):
    assert resolve_recommended_action(proposed, RiskLevel.MEDIUM, True) == (proposed, False)


def test_less_cautious_proposal_is_raised_to_the_minimum():
    assert resolve_recommended_action("APPROVE_PAYMENT", RiskLevel.HIGH, True) == (A.HOLD_PAYMENT, True)


def test_escalate_is_below_hold():
    assert resolve_recommended_action("ESCALATE", RiskLevel.CRITICAL, True) == (A.HOLD_PAYMENT, True)


def test_low_risk_unverified_supplier_cannot_be_approved():
    assert resolve_recommended_action("APPROVE_PAYMENT", RiskLevel.LOW, False) == (A.REQUEST_VERIFICATION, True)


@pytest.mark.parametrize("junk", [None, "", "PAY_NOW", "approve_payment", 42, {"action": "HOLD_PAYMENT"}])
def test_invalid_proposal_falls_back_to_the_minimum(junk):
    assert resolve_recommended_action(junk, RiskLevel.LOW, True) == (A.APPROVE_PAYMENT, True)


def test_fallback_recommendation_uses_minimum_action_and_says_ai_was_unavailable():
    action, text = fallback_recommendation(RiskLevel.HIGH, supplier_verified=True, ai_available=False)
    assert action == A.HOLD_PAYMENT
    assert "AI review was unavailable" in text
    assert "HIGH" in text


def test_fallback_without_score_escalates():
    action, _ = fallback_recommendation(None, supplier_verified=True, ai_available=False)
    assert action == A.ESCALATE


# --- AI indicator whitelist --------------------------------------------------------------


def test_ai_may_only_contribute_qualitative_types():
    assert frozenset({"SOCIAL_ENGINEERING", "DOCUMENT_ANOMALY", "OTHER"}) == AI_INDICATOR_TYPES


def test_filter_keeps_allowed_types_and_reports_the_rest():
    proposed = [
        {"type": "SOCIAL_ENGINEERING", "description": "Says 'do not call us'", "severity": "HIGH"},
        {"type": "BANK_DETAILS_CHANGED", "description": "model tries to set a fact", "severity": "CRITICAL"},
        {"type": "CONTRACT_DEVIATION", "description": "decided by code, not the model", "severity": "HIGH"},
        {"type": "DOCUMENT_ANOMALY", "description": "VAT doesn't add up", "severity": "bogus"},
        {"type": "OTHER"},  # no description
        "not even a dict",
    ]
    accepted, ignored = filter_ai_indicators(proposed)
    assert [i.type for i in accepted] == ["SOCIAL_ENGINEERING", "DOCUMENT_ANOMALY"]
    assert all(i.source == IndicatorSource.AI for i in accepted)
    assert accepted[1].severity == Severity.MEDIUM  # invalid severity defaults to MEDIUM
    assert ignored == ["BANK_DETAILS_CHANGED", "CONTRACT_DEVIATION", "OTHER", "<invalid>"]


def test_filter_accepts_pydantic_like_objects():
    class Obs:
        type = "SOCIAL_ENGINEERING"
        description = "Claims CEO approved it"
        severity = "HIGH"

    accepted, ignored = filter_ai_indicators([Obs()])
    assert [i.type for i in accepted] == ["SOCIAL_ENGINEERING"] and ignored == []


# --- bank-details change always means HOLD (Bennet's policy decision, ADR-029) ------------


@pytest.mark.parametrize("level", [RiskLevel.LOW, RiskLevel.MEDIUM, RiskLevel.HIGH, RiskLevel.CRITICAL, None])
def test_bank_details_change_requires_hold_at_any_level(level):
    assert minimum_action(level, True, findings={"BANK_DETAILS_CHANGED"}) == A.HOLD_PAYMENT


def test_other_findings_do_not_change_the_floor():
    assert minimum_action(RiskLevel.MEDIUM, True, findings={"THRESHOLD_AVOIDANCE"}) == A.REQUEST_VERIFICATION


def test_model_cannot_lower_a_bank_change_below_hold():
    action, raised = resolve_recommended_action(
        "REQUEST_VERIFICATION", RiskLevel.MEDIUM, True, findings={"BANK_DETAILS_CHANGED"}
    )
    assert (action, raised) == (A.HOLD_PAYMENT, True)


def test_fallback_for_bank_change_holds_and_says_why():
    action, text = fallback_recommendation(
        RiskLevel.MEDIUM, supplier_verified=True, ai_available=True, findings={"BANK_DETAILS_CHANGED"}
    )
    assert action == A.HOLD_PAYMENT
    assert "phone" in text and "email" in text
