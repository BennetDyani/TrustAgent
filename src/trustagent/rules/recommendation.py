"""What the model is allowed to contribute, and the minimum-action rule.

The model may propose an action; code makes sure it is never less cautious
than the risk level requires. The model may report observations; code keeps
only the qualitative types rules can't see.
"""

from collections.abc import Iterable
from typing import Any

from trustagent.domain import Action, Caution, IndicatorSource, RiskIndicator, RiskLevel, Severity

# Indicator types the AI may contribute. Everything else is decided by rules
# (or, for CONTRACT_DEVIATION, by code comparing against retrieved contract
# terms; ADR-003). Any other type the model reports is ignored and logged.
AI_INDICATOR_TYPES = frozenset({"SOCIAL_ENGINEERING", "DOCUMENT_ANOMALY", "OTHER"})


# Findings that require HOLD whatever the score (policy decision, ADR-029): a changed
# bank account on a verified supplier is held until finance confirms the new account
# by phone AND email, using contact details from the onboarding records.
HOLD_REQUIRED_FINDINGS = frozenset({"BANK_DETAILS_CHANGED"})


def minimum_action(level: RiskLevel | None, supplier_verified: bool, findings: Iterable[str] = ()) -> Action:
    """The least cautious action allowed.

    ``findings`` are the indicator types found. Some force HOLD regardless of
    the score. An unverified supplier is never recommended for straight
    approval, however low the score.
    """
    if HOLD_REQUIRED_FINDINGS & set(findings):
        return Action.HOLD_PAYMENT
    match level:
        case RiskLevel.CRITICAL | RiskLevel.HIGH:
            return Action.HOLD_PAYMENT
        case RiskLevel.MEDIUM:
            return Action.REQUEST_VERIFICATION
        case RiskLevel.LOW:
            return Action.APPROVE_PAYMENT if supplier_verified else Action.REQUEST_VERIFICATION
        case _:
            return Action.ESCALATE


def resolve_recommended_action(
    proposed: Any, level: RiskLevel | None, supplier_verified: bool, findings: Iterable[str] = ()
) -> tuple[Action, bool]:
    """Accept the model's action only if it is at least as cautious as the minimum.

    Returns ``(action, raised)``, where ``raised`` is True when the proposal was
    missing, invalid or too lenient and the minimum was used instead.
    """
    floor = minimum_action(level, supplier_verified, findings)
    try:
        action = Action(proposed)
    except (ValueError, TypeError):
        return floor, True
    if Caution[action.value] >= Caution[floor.value]:
        return action, False
    return floor, True


_FALLBACK_TEXT = {
    Action.HOLD_PAYMENT: "Recommend holding payment pending independent verification.",
    Action.REQUEST_VERIFICATION: "Recommend requesting verification before processing.",
    Action.APPROVE_PAYMENT: "No significant issues found; recommend normal processing.",
    Action.ESCALATE: "The investigation could not reach a risk determination. Recommend escalation for manual review.",
}


BANK_CHANGE_TEXT = (
    "The bank account differs from the verified record (POL-001). Payment must stay on hold until the finance "
    "team confirms the new account by phone AND email, using contact details from the original onboarding "
    "records, never those printed on the invoice."
)


def fallback_recommendation(
    level: RiskLevel | None, supplier_verified: bool, ai_available: bool, findings: Iterable[str] = ()
) -> tuple[Action, str]:
    """Rule-only recommendation, used when the model is unavailable or fails."""
    findings = set(findings)
    action = minimum_action(level, supplier_verified, findings)
    parts = []
    if level is not None:
        parts.append(f"Risk level is {level.value}.")
    parts.append(BANK_CHANGE_TEXT if HOLD_REQUIRED_FINDINGS & findings else _FALLBACK_TEXT[action])
    if not ai_available:
        parts.append(
            "The AI review was unavailable, so this assessment is based on the deterministic rule checks only. "
            "Re-run the investigation once the AI model is available for a full assessment."
        )
    return action, " ".join(parts)


def _field(obs: Any, name: str) -> Any:
    return obs.get(name) if isinstance(obs, dict) else getattr(obs, name, None)


def filter_ai_indicators(proposed: Iterable[Any]) -> tuple[list[RiskIndicator], list[str]]:
    """Keep only whitelisted AI observation types with a description.

    Returns ``(accepted, ignored_types)``. Accepted indicators are tagged
    ``source=AI``; an invalid severity defaults to MEDIUM.
    """
    accepted: list[RiskIndicator] = []
    ignored: list[str] = []
    for obs in proposed:
        type_ = _field(obs, "type")
        description = _field(obs, "description")
        if not isinstance(type_, str):
            ignored.append("<invalid>")
            continue
        if type_ not in AI_INDICATOR_TYPES or not isinstance(description, str) or not description.strip():
            ignored.append(type_)
            continue
        severity = _field(obs, "severity")
        severity = str(getattr(severity, "value", severity) or "").upper()
        accepted.append(
            RiskIndicator(
                type=type_,
                description=description.strip(),
                severity=Severity(severity) if severity in Severity.__members__ else Severity.MEDIUM,
                source=IndicatorSource.AI,
            )
        )
    return accepted, ignored
