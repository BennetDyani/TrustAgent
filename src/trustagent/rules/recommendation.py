"""What the model is allowed to contribute, and the minimum-action rule.

The model may propose an action; code makes sure it is never less cautious
than the risk level requires. The model may report observations; code keeps
only the qualitative types rules can't see.
"""

from collections.abc import Iterable
from decimal import Decimal
from typing import Any

from trustagent.config import get_settings
from trustagent.domain import Action, Caution, IndicatorSource, RiskIndicator, RiskLevel, Severity
from trustagent.text import normalise_text

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


def required_next_steps(findings: Iterable[str], supplier_verified: bool, amount: Decimal) -> list[str]:
    """What the reviewer must do next, decided by code from the findings (ADR-040).

    The report writer may phrase these but not add to them. In the live run the model copied the
    bank-change instruction into cases with no bank change.
    """
    findings = set(findings)
    steps: list[str] = []
    if "BANK_DETAILS_CHANGED" in findings:
        steps.append(
            "Keep the payment on hold. Finance must confirm the new bank account by phone AND email, using the "
            "contact details in the original onboarding records (never those on the invoice), and then mark the "
            "supplier verified (POL-001)."
        )
    if "SUPPLIER_NOT_VERIFIED" in findings or not supplier_verified:
        steps.append(
            "Complete new-supplier onboarding: confirm the company, its bank account and its contacts by phone and "
            "email using an independent source such as the company register, never the invoice."
        )
    if findings & {"ACCOUNT_HOLDER_MISMATCH", "EMAIL_DOMAIN_MISMATCH", "PERSONAL_EMAIL_DOMAIN"}:
        steps.append(
            "Confirm with the supplier, through its known contacts, that the account holder and contact email on "
            "the invoice are genuinely theirs."
        )
    if "MULTIPLE_BANK_ACCOUNTS" in findings:
        steps.append("Confirm with the supplier, through known contacts, which single bank account is correct.")
    if "SHARED_BANK_ACCOUNT" in findings:
        steps.append(
            "Find out why this bank account is also linked to another supplier (named in the evidence) before paying."
        )
    if "CONTRACT_DEVIATION" in findings:
        steps.append(
            "Query the price with the supplier; the contract owner must approve any price above the contract rate "
            "in writing before payment (policy manual 10.2)."
        )
    if "DUPLICATE_INVOICE" in findings:
        steps.append("Check that the earlier invoice named in the evidence has not already been paid.")
    if findings & {"UNUSUAL_AMOUNT", "PATTERN_ANOMALY", "THRESHOLD_AVOIDANCE"}:
        steps.append("Confirm with the requesting department that the goods or services and the amount are expected.")
    if amount > get_settings().large_transaction_threshold:
        steps.append("POL-002: approval needs both a Finance Manager and a Department Head, two different people.")
    return steps or ["No further checks are required; the payment can go through normal approval."]


MIN_QUOTE_CHARS = 8


def quote_supported(quote: str | None, document: str) -> bool:
    """Does the model's quote really appear in the document? (ADR-041)

    Lenient about case, whitespace, markdown bold, typographic quotes and a
    "..." where the model skipped words. Strict about the words themselves.
    """
    if not quote or not quote.strip():
        return False
    doc = normalise_text(document)
    parts = [p.strip(" .") for p in normalise_text(quote).split("...")]
    parts = [p for p in parts if p]
    if sum(len(p) for p in parts) < MIN_QUOTE_CHARS:
        return False
    return all(p in doc for p in parts)


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
                overlaps=overlaps if isinstance(overlaps := _field(obs, "relates_to_rule"), str) else None,
            )
        )
    return accepted, ignored
