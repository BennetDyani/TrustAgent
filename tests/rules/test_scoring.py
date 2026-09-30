import pytest

from trustagent.domain import IndicatorSource, RiskIndicator, RiskLevel, Severity
from trustagent.rules.scoring import RISK_WEIGHTS, calculate_risk, classify_level


def ind(type_: str, source=IndicatorSource.RULE) -> RiskIndicator:
    return RiskIndicator(type=type_, description=type_, severity=Severity.MEDIUM, source=source)


def test_weights_match_the_reference_table():
    assert RISK_WEIGHTS == {
        "BANK_DETAILS_CHANGED": 30,
        "DUPLICATE_INVOICE": 35,
        "ACCOUNT_HOLDER_MISMATCH": 25,
        "EMAIL_DOMAIN_MISMATCH": 25,
        "UNUSUAL_AMOUNT": 20,
        "PERSONAL_EMAIL_DOMAIN": 15,
        "SUPPLIER_NOT_VERIFIED": 15,
        "AMOUNT_EXCEEDS_THRESHOLD": 15,
        "THRESHOLD_AVOIDANCE": 15,
        "PATTERN_ANOMALY": 10,
        "URGENCY_INDICATOR": 10,
        "SOCIAL_ENGINEERING": 20,
        "CONTRACT_DEVIATION": 20,
        "DOCUMENT_ANOMALY": 10,
        "OTHER": 5,
        "CONFIRMED_MATCH": 0,
    }


@pytest.mark.parametrize(
    "score, level",
    [
        (0, "LOW"),
        (29, "LOW"),
        (30, "MEDIUM"),
        (59, "MEDIUM"),
        (60, "HIGH"),
        (79, "HIGH"),
        (80, "CRITICAL"),
        (100, "CRITICAL"),
    ],
)
def test_level_boundaries(score, level):
    assert classify_level(score) == RiskLevel(level)


def test_no_indicators_is_low_zero():
    result = calculate_risk([])
    assert (result.score, result.level, result.indicators) == (0, RiskLevel.LOW, [])


def test_each_type_counts_once_and_repeats_are_kept_with_weight_zero():
    result = calculate_risk([ind("SOCIAL_ENGINEERING")] * 3)
    assert result.score == 20
    assert [i.weight for i in result.indicators] == [20, 0, 0]


def test_score_is_capped_at_100():
    result = calculate_risk(
        [
            ind(t)
            for t in ("DUPLICATE_INVOICE", "BANK_DETAILS_CHANGED", "ACCOUNT_HOLDER_MISMATCH", "EMAIL_DOMAIN_MISMATCH")
        ]
    )
    assert result.score == 100  # 115 before the cap
    assert result.level == RiskLevel.CRITICAL


def test_confirmed_matches_score_zero_but_stay_as_evidence():
    result = calculate_risk([ind("CONFIRMED_MATCH"), ind("CONFIRMED_MATCH")])
    assert result.score == 0
    assert len(result.indicators) == 2


def test_unknown_type_scores_as_other():
    assert calculate_risk([ind("SOMETHING_NEW")]).score == 5


def test_sample_inv_1048_is_high():
    # Bank change 30 + unusual amount 20 + urgency 10 = 60 -> HIGH
    result = calculate_risk(
        [ind("BANK_DETAILS_CHANGED"), ind("UNUSUAL_AMOUNT"), ind("URGENCY_INDICATOR"), ind("CONFIRMED_MATCH")]
    )
    assert (result.score, result.level) == (60, RiskLevel.HIGH)


def test_indicator_order_and_source_are_preserved():
    inds = [ind("URGENCY_INDICATOR"), ind("SOCIAL_ENGINEERING", IndicatorSource.AI)]
    result = calculate_risk(inds)
    assert [(i.type, i.source) for i in result.indicators] == [(i.type, i.source) for i in inds]
