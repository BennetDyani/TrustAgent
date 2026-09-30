"""Deterministic rule checks.

These establish the verifiable facts of an investigation: does the bank
account match, is the amount normal, is this a duplicate. They run in code so
the result is repeatable and never depends on a language model reading a
number correctly. Every function here is pure: same input, same output, no I/O.
"""

import re
from dataclasses import dataclass, field
from decimal import Decimal

from trustagent.config import get_settings
from trustagent.domain import (
    IndicatorSource,
    Invoice,
    InvoiceStatus,
    RiskIndicator,
    Severity,
    Supplier,
    Transaction,
    TransactionStatus,
    Urgency,
)
from trustagent.rules.matching import name_similarity

PERSONAL_EMAIL_DOMAINS = frozenset(
    {
        "gmail.com", "googlemail.com", "yahoo.com", "ymail.com", "outlook.com", "hotmail.com",
        "live.com", "icloud.com", "aol.com", "proton.me", "protonmail.com", "gmx.com", "mail.com",
    }
)  # fmt: skip

_EMAIL_DOMAIN = re.compile(r"@([a-z0-9.-]+\.[a-z]{2,})\s*$")


@dataclass(frozen=True)
class RulePolicy:
    """Thresholds from POL-001..POL-004. Passed in, so tests can vary them."""

    large_transaction_threshold: Decimal = Decimal("100000")  # POL-002
    threshold_avoidance_band: Decimal = Decimal("0.05")
    pattern_multiplier: Decimal = Decimal("3")  # POL-003
    min_history_for_pattern: int = 3
    holder_match_threshold: float = 0.5

    @classmethod
    def from_settings(cls) -> "RulePolicy":
        s = get_settings()
        return cls(
            large_transaction_threshold=s.large_transaction_threshold,
            threshold_avoidance_band=s.threshold_avoidance_band,
            pattern_multiplier=s.pattern_multiplier,
            min_history_for_pattern=s.min_history_for_pattern,
            holder_match_threshold=s.holder_match_threshold,
        )


@dataclass(frozen=True)
class CheckContext:
    invoice: Invoice
    supplier: Supplier | None
    history: list[Transaction] = field(default_factory=list)
    other_invoices: list[Invoice] = field(default_factory=list)


# --- helpers -------------------------------------------------------------------------


def last4(account: str | None) -> str:
    """Last four digits of an account number, ignoring spaces, dashes and masking."""
    return re.sub(r"\D", "", account or "")[-4:]


def mask_account(account: str | None) -> str:
    """Masked form for storage and logs (POPIA): ``****1234``."""
    digits = last4(account)
    return f"****{digits}" if digits else "****"


def email_domain(email: str | None) -> str | None:
    match = _EMAIL_DOMAIN.search((email or "").lower())
    return match.group(1) if match else None


def rands(amount: Decimal) -> str:
    text = f"{amount:,.2f}"
    return f"R{text.removesuffix('.00')}"


def _rule(type_: str, severity: Severity, description: str) -> RiskIndicator:
    return RiskIndicator(type=type_, severity=severity, description=description, source=IndicatorSource.RULE)


def _confirmed(description: str) -> RiskIndicator:
    return _rule("CONFIRMED_MATCH", Severity.LOW, description)


def _line_item_signature(invoice: Invoice) -> str:
    """Line-item descriptions, normalised and order-independent."""
    return "|".join(sorted(re.sub(r"\s+", " ", li.description.lower()).strip() for li in invoice.line_items))


# --- checks --------------------------------------------------------------------------


def check_supplier_and_bank(ctx: CheckContext, policy: RulePolicy) -> list[RiskIndicator]:
    inv, sup = ctx.invoice, ctx.supplier
    if sup is None:
        return [_rule("SUPPLIER_NOT_VERIFIED", Severity.HIGH, "No supplier record exists for this invoice.")]
    if not sup.verified:
        # Onboarding, NOT a bank change: there is no confirmed record to compare against.
        return [
            _rule(
                "SUPPLIER_NOT_VERIFIED",
                Severity.MEDIUM,
                f"{sup.name} is not a verified supplier, so there is no confirmed banking record to compare "
                "against. Treat as new-supplier onboarding.",
            )
        ]
    if last4(inv.bank_account) != last4(sup.bank_account):
        return [
            _rule(
                "BANK_DETAILS_CHANGED",
                Severity.CRITICAL,
                f"Invoice bank account {mask_account(inv.bank_account)} ({inv.bank_name or 'bank not stated'}) does "
                f"not match the verified account on file {mask_account(sup.bank_account)} ({sup.bank_name}). "
                "POL-001 requires independent phone verification before payment.",
            )
        ]
    return [
        _confirmed(
            f"Bank account {mask_account(inv.bank_account)} matches the verified account on file for {sup.name}."
        )
    ]


def check_account_holder(ctx: CheckContext, policy: RulePolicy) -> list[RiskIndicator]:
    holder = ctx.invoice.bank_account_holder
    if not holder:
        return []
    expected = ctx.supplier.name if ctx.supplier else ctx.invoice.supplier_name
    if name_similarity(holder, expected) < policy.holder_match_threshold:
        return [
            _rule(
                "ACCOUNT_HOLDER_MISMATCH",
                Severity.HIGH,
                f'Bank account holder "{holder}" does not match the supplier name "{expected}". Payments to a '
                "differently named account are a common redirection tactic.",
            )
        ]
    return [_confirmed(f'Bank account holder "{holder}" matches the supplier name.')]


def check_email_domain(ctx: CheckContext, policy: RulePolicy) -> list[RiskIndicator]:
    domain = email_domain(ctx.invoice.supplier_email)
    if not domain:
        return []
    if domain in PERSONAL_EMAIL_DOMAINS:
        return [
            _rule(
                "PERSONAL_EMAIL_DOMAIN",
                Severity.HIGH,
                f"Invoice contact {ctx.invoice.supplier_email} uses a personal email provider ({domain}), "
                "not a company domain.",
            )
        ]
    sup = ctx.supplier
    record_domain = email_domain(sup.contact_email) if sup and sup.verified else None
    if not record_domain:
        return []
    if domain != record_domain:
        return [
            _rule(
                "EMAIL_DOMAIN_MISMATCH",
                Severity.HIGH,
                f'Invoice contact domain "{domain}" differs from the verified supplier domain "{record_domain}" '
                "- a possible lookalike domain.",
            )
        ]
    return [_confirmed(f'Invoice contact domain "{domain}" matches the supplier record.')]


def check_amount(ctx: CheckContext, policy: RulePolicy) -> list[RiskIndicator]:
    amount, sup = ctx.invoice.amount, ctx.supplier

    # A verified supplier-specific range takes precedence over generic limits:
    # a large invoice that is normal for THIS supplier is not itself a signal.
    if sup and sup.verified and sup.expected_spend_min is not None and sup.expected_spend_max is not None:
        lo, hi = sup.expected_spend_min, sup.expected_spend_max
        if amount > hi:
            return [
                _rule(
                    "UNUSUAL_AMOUNT",
                    Severity.HIGH,
                    f"Amount {rands(amount)} exceeds this supplier's expected range of {rands(lo)}-{rands(hi)}.",
                )
            ]
        return [_confirmed(f"Amount {rands(amount)} is within the expected range of {rands(lo)}-{rands(hi)}.")]

    findings: list[RiskIndicator] = []

    completed = [t.amount for t in ctx.history if t.status == TransactionStatus.COMPLETED]
    if len(completed) >= policy.min_history_for_pattern:
        average = sum(completed, Decimal(0)) / len(completed)
        if amount > average * policy.pattern_multiplier:
            findings.append(
                _rule(
                    "PATTERN_ANOMALY",
                    Severity.HIGH,
                    f"Amount {rands(amount)} is {amount / average:.1f}x the supplier's historical average of "
                    f"{rands(average.quantize(Decimal(1)))} (POL-003 flags over {policy.pattern_multiplier}x).",
                )
            )
        else:
            findings.append(
                _confirmed(
                    f"Amount {rands(amount)} is consistent with the historical average of "
                    f"{rands(average.quantize(Decimal(1)))}."
                )
            )

    threshold = policy.large_transaction_threshold
    if amount > threshold:
        findings.append(
            _rule(
                "AMOUNT_EXCEEDS_THRESHOLD",
                Severity.MEDIUM,
                f"Amount {rands(amount)} exceeds the {rands(threshold)} threshold with no supplier-specific range "
                "on file. POL-002 requires dual authorization.",
            )
        )
    elif amount >= threshold * (1 - policy.threshold_avoidance_band):
        findings.append(
            _rule(
                "THRESHOLD_AVOIDANCE",
                Severity.MEDIUM,
                f"Amount {rands(amount)} sits just below the {rands(threshold)} dual-authorization threshold - "
                "a common pattern for avoiding extra approval.",
            )
        )
    return findings


def check_urgency(ctx: CheckContext, policy: RulePolicy) -> list[RiskIndicator]:
    if ctx.invoice.urgency != Urgency.IMMEDIATE:
        return []
    return [
        _rule(
            "URGENCY_INDICATOR",
            Severity.MEDIUM,
            "Payment is marked IMMEDIATE/urgent. POL-004 treats urgency as a social-engineering signal "
            "requiring extra scrutiny.",
        )
    ]


def check_duplicate(ctx: CheckContext, policy: RulePolicy) -> list[RiskIndicator]:
    """Same supplier, and identical line items or the same amount on the same date.

    Recurring monthly invoices name the month in their line items and have
    different dates, so they are not flagged.
    """
    inv = ctx.invoice
    if inv.supplier_id is None:
        return []
    signature = _line_item_signature(inv)
    for other in ctx.other_invoices:
        if other.id == inv.id or other.supplier_id != inv.supplier_id or other.status == InvoiceStatus.REJECTED:
            continue
        same_items = signature != "" and _line_item_signature(other) == signature
        same_amount_and_date = other.amount == inv.amount and inv.date is not None and other.date == inv.date
        if same_items or same_amount_and_date:
            return [
                _rule(
                    "DUPLICATE_INVOICE",
                    Severity.HIGH,
                    f"Possible duplicate of invoice {other.invoice_number} ({rands(other.amount)}, {other.date}) "
                    "from the same supplier with the same billed items."
                    if same_items
                    else f"Possible duplicate of invoice {other.invoice_number}: same supplier, same amount "
                    f"({rands(other.amount)}) on the same date ({other.date}).",
                )
            ]
    return []


RULE_CHECKS = (
    check_supplier_and_bank,
    check_account_holder,
    check_email_domain,
    check_amount,
    check_urgency,
    check_duplicate,
)


def run_rule_checks(ctx: CheckContext, policy: RulePolicy | None = None) -> list[RiskIndicator]:
    """Run every rule check in a fixed order. Includes CONFIRMED_MATCH entries for passed checks."""
    policy = policy or RulePolicy.from_settings()
    return [indicator for check in RULE_CHECKS for indicator in check(ctx, policy)]
