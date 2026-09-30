"""Deterministic risk calculator. The score always comes from here, never from the model."""

from collections.abc import Iterable

from trustagent.config import get_settings
from trustagent.domain import IndicatorSource, RiskIndicator, RiskLevel, RiskResult, ScoredIndicator

RISK_WEIGHTS: dict[str, int] = {
    # Rule checks (rules/checks.py)
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
    # AI observations (whitelisted in rules/recommendation.py)
    "SOCIAL_ENGINEERING": 20,
    # Code comparison against RAG-retrieved contract terms (ADR-003)
    "CONTRACT_DEVIATION": 20,
    "DOCUMENT_ANOMALY": 10,
    "OTHER": 5,
    # A checked dimension that came back clean
    "CONFIRMED_MATCH": 0,
}

MAX_SCORE = 100


def classify_level(score: int) -> RiskLevel:
    if score >= 80:
        return RiskLevel.CRITICAL
    if score >= 60:
        return RiskLevel.HIGH
    if score >= 30:
        return RiskLevel.MEDIUM
    return RiskLevel.LOW


def calculate_risk(indicators: Iterable[RiskIndicator], ai_cap: int | None = None) -> RiskResult:
    """Sum the weights, counting each indicator type once, capped at 100.

    - Repeats of a type (e.g. three separate "pressure" phrases) are kept as
      evidence with weight 0, so one concern reported many times can't inflate
      the score.
    - An AI observation that overlaps a rule finding which actually fired scores
      0: the concern is already counted (ADR-042). Code checks the model's claim.
    - AI findings together add at most ``ai_cap`` points (ADR-043). Rule weights
      are never capped. Trimmed AI indicators stay as evidence.
    """
    indicators = list(indicators)
    ai_cap = get_settings().ai_score_cap if ai_cap is None else ai_cap
    fired_rules = {i.type for i in indicators if i.source != IndicatorSource.AI and i.type != "CONFIRMED_MATCH"}
    counted: set[str] = set()
    ai_total = 0
    scored: list[ScoredIndicator] = []
    for indicator in indicators:
        is_ai = indicator.source == IndicatorSource.AI
        if is_ai and indicator.overlaps in fired_rules:
            weight = 0  # already scored by the rule; doesn't use up the type
        elif indicator.type in counted:
            weight = 0
        else:
            weight = RISK_WEIGHTS.get(indicator.type, RISK_WEIGHTS["OTHER"])
            counted.add(indicator.type)
        if is_ai and weight:
            weight = min(weight, max(ai_cap - ai_total, 0))
            ai_total += weight
        scored.append(ScoredIndicator(**indicator.model_dump(), weight=weight))

    score = min(sum(i.weight for i in scored), MAX_SCORE)
    return RiskResult(score=score, level=classify_level(score), indicators=scored)
