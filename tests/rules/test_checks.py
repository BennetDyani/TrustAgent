"""Deterministic rule checks: behaviour ported from the reference checks.ts."""

import datetime as dt
from decimal import Decimal

import pytest

from tests.factories import invoice, seed_history, seed_supplier, txn
from trustagent.domain import IndicatorSource, InvoiceStatus, LineItem, Severity, Supplier, Urgency
from trustagent.rules.checks import CheckContext, RulePolicy, last4, run_rule_checks


def types(indicators) -> list[str]:
    return [i.type for i in indicators]


def flagged(indicators) -> set[str]:
    """Indicator types excluding passed checks."""
    return {i.type for i in indicators if i.type != "CONFIRMED_MATCH"}


def run(inv, supplier="SUP-001", history=None, others=None, **supplier_overrides):
    sup = seed_supplier(supplier, **supplier_overrides) if isinstance(supplier, str) else supplier
    return run_rule_checks(
        CheckContext(
            invoice=inv,
            supplier=sup,
            history=seed_history(sup.id) if history is None and sup else (history or []),
            other_invoices=others or [],
        )
    )


# --- helpers -------------------------------------------------------------------


@pytest.mark.parametrize(
    "raw, expected",
    [("1092847591 9917", "9917"), ("****4821", "4821"), ("0412-0398-2177-33", "7733"), ("", ""), (None, "")],
)
def test_last4_uses_digits_only(raw, expected):
    assert last4(raw) == expected


def test_policy_defaults_come_from_settings():
    policy = RulePolicy.from_settings()
    assert policy.large_transaction_threshold == Decimal("100000")
    assert policy.pattern_multiplier == Decimal("3")


def test_every_finding_is_a_rule_finding():
    inds = run(invoice(amount=Decimal("185000"), bank_account="****9917", urgency=Urgency.IMMEDIATE))
    assert all(i.source == IndicatorSource.RULE for i in inds)


# --- clean invoice ---------------------------------------------------------------


def test_clean_invoice_from_verified_supplier_only_confirms():
    inds = run(invoice())
    assert flagged(inds) == set()
    # Passed checks are kept as evidence so the report can say *why* it's safe.
    assert types(inds).count("CONFIRMED_MATCH") >= 4


# --- supplier and bank (POL-001) -------------------------------------------------


def test_bank_details_changed_for_verified_supplier():
    inds = run(invoice(bank_account="****9917", bank_name="Capitec Bank"))
    bank = next(i for i in inds if i.type == "BANK_DETAILS_CHANGED")
    assert bank.severity == Severity.CRITICAL
    assert "POL-001" in bank.description


def test_unverified_supplier_is_onboarding_not_a_bank_change():
    inds = run(invoice(bank_account="****1111"), verified=False)
    assert "SUPPLIER_NOT_VERIFIED" in flagged(inds)
    assert "BANK_DETAILS_CHANGED" not in flagged(inds)


def test_missing_supplier_record_is_not_verified_high():
    inds = run(invoice(supplier_id=None), supplier=None)
    finding = next(i for i in inds if i.type == "SUPPLIER_NOT_VERIFIED")
    assert finding.severity == Severity.HIGH
    assert "BANK_DETAILS_CHANGED" not in flagged(inds)


def test_bank_match_compares_last4_regardless_of_formatting():
    inds = run(invoice(bank_account="6244 0193 8248 21"))
    assert "BANK_DETAILS_CHANGED" not in flagged(inds)


# --- account holder ----------------------------------------------------------------


def test_account_holder_with_entity_suffix_matches():
    assert "ACCOUNT_HOLDER_MISMATCH" not in flagged(run(invoice(bank_account_holder="ABC Office Solutions (Pty) Ltd")))


def test_account_holder_mismatch():
    inds = run(invoice(bank_account_holder="J Mokoena Trading"))
    assert "ACCOUNT_HOLDER_MISMATCH" in flagged(inds)


def test_account_holder_for_unknown_supplier_compares_to_invoice_name():
    inv = invoice(supplier_id=None, supplier_name="QuickShip Logistics", bank_account_holder="Q-Ship Trading")
    assert "ACCOUNT_HOLDER_MISMATCH" in flagged(run(inv, supplier=None))


def test_account_holder_at_exact_threshold_passes():
    # Reference behaviour: similarity 0.5 is NOT < 0.5. "Prestige Events Holdings" vs
    # "Prestige Catering & Events" shares 2 of 4 tokens. Kept deliberately; see DECISIONS.md.
    inv = invoice(
        supplier_id=None, supplier_name="Prestige Catering & Events", bank_account_holder="Prestige Events Holdings"
    )
    assert "ACCOUNT_HOLDER_MISMATCH" not in flagged(run(inv, supplier=None))


def test_no_account_holder_on_invoice_means_no_holder_check():
    inds = run(invoice(bank_account_holder=None))
    assert not any("holder" in i.description.lower() for i in inds)


# --- email domain ------------------------------------------------------------------


def test_lookalike_domain_on_verified_supplier():
    inds = run(invoice(supplier_email="accounts@abc-office.co.za"))
    assert "EMAIL_DOMAIN_MISMATCH" in flagged(inds)


def test_email_domain_is_case_insensitive():
    assert "EMAIL_DOMAIN_MISMATCH" not in flagged(run(invoice(supplier_email="Accounts@ABCOffice.co.za")))


@pytest.mark.parametrize("domain", ["gmail.com", "outlook.com", "yahoo.com", "hotmail.com", "proton.me"])
def test_personal_email_domain(domain):
    inds = run(invoice(supplier_email=f"abc.accounts@{domain}"))
    assert "PERSONAL_EMAIL_DOMAIN" in flagged(inds)
    # A personal domain is reported as that, not additionally as a lookalike.
    assert "EMAIL_DOMAIN_MISMATCH" not in flagged(inds)


def test_domain_mismatch_needs_a_verified_record():
    inds = run(invoice(supplier_email="accounts@other.co.za"), verified=False)
    assert "EMAIL_DOMAIN_MISMATCH" not in flagged(inds)


def test_missing_or_malformed_email_is_skipped():
    for email in (None, "", "not-an-email"):
        assert not {"EMAIL_DOMAIN_MISMATCH", "PERSONAL_EMAIL_DOMAIN"} & flagged(run(invoice(supplier_email=email)))


# --- amount ------------------------------------------------------------------------


def test_amount_above_supplier_range_is_unusual():
    inds = run(invoice(amount=Decimal("40000.01")))
    assert "UNUSUAL_AMOUNT" in flagged(inds)


def test_amount_at_range_maximum_is_fine():
    assert "UNUSUAL_AMOUNT" not in flagged(run(invoice(amount=Decimal("40000"))))


def test_supplier_range_takes_precedence_over_generic_checks():
    # R185,000 for ABC: above its range, so UNUSUAL_AMOUNT only. No threshold or
    # pattern finding, because a verified supplier range replaces those checks.
    f = flagged(run(invoice(amount=Decimal("185000"))))
    assert "UNUSUAL_AMOUNT" in f
    assert not {"AMOUNT_EXCEEDS_THRESHOLD", "PATTERN_ANOMALY", "THRESHOLD_AVOIDANCE"} & f


def test_threshold_exceeded_without_supplier_range():
    # Digital Print Co is verified but has no expected range on file.
    inv = invoice(
        supplier_id="SUP-003",
        supplier_name="Digital Print Co",
        amount=Decimal("195500"),
        bank_account="****2190",
        bank_account_holder=None,
        supplier_email=None,
    )
    f = flagged(run(inv, supplier="SUP-003"))
    assert "AMOUNT_EXCEEDS_THRESHOLD" in f
    assert "UNUSUAL_AMOUNT" not in f


def test_unverified_supplier_range_is_ignored():
    f = flagged(run(invoice(amount=Decimal("150000")), verified=False))
    assert "AMOUNT_EXCEEDS_THRESHOLD" in f
    assert "UNUSUAL_AMOUNT" not in f


@pytest.mark.parametrize(
    "amount, expected",
    [
        ("94999.99", set()),
        ("95000", {"THRESHOLD_AVOIDANCE"}),
        ("99999.99", {"THRESHOLD_AVOIDANCE"}),
        ("100000", {"THRESHOLD_AVOIDANCE"}),  # "over R100,000" is strict, so exactly R100k is not over
        ("100000.01", {"AMOUNT_EXCEEDS_THRESHOLD"}),
    ],
)
def test_threshold_boundaries(amount, expected):
    unknown = Supplier(id="SUP-X", name="New Co", bank_account="****1234", verified=False)
    inv = invoice(
        supplier_id="SUP-X",
        supplier_name="New Co",
        amount=Decimal(amount),
        bank_account="****1234",
        bank_account_holder=None,
        supplier_email=None,
    )
    f = flagged(run(inv, supplier=unknown, history=[]))
    assert f & {"THRESHOLD_AVOIDANCE", "AMOUNT_EXCEEDS_THRESHOLD"} == expected


def _no_range_supplier():
    return Supplier(id="SUP-X", name="New Co", bank_account="****1234", verified=True)


def _pattern_invoice(amount):
    return invoice(
        supplier_id="SUP-X",
        supplier_name="New Co",
        amount=Decimal(amount),
        bank_account="****1234",
        bank_account_holder=None,
        supplier_email=None,
    )


def test_pattern_anomaly_over_three_times_average():
    history = [txn("10000", n=i) for i in range(3)]
    assert "PATTERN_ANOMALY" in flagged(
        run(_pattern_invoice("30000.01"), supplier=_no_range_supplier(), history=history)
    )


def test_pattern_at_exactly_three_times_is_fine():
    history = [txn("10000", n=i) for i in range(3)]
    assert "PATTERN_ANOMALY" not in flagged(
        run(_pattern_invoice("30000"), supplier=_no_range_supplier(), history=history)
    )


def test_pattern_needs_three_completed_payments():
    history = [txn("10000", n=0), txn("10000", n=1), txn("10000", status="FAILED", n=2)]
    inds = run(_pattern_invoice("90000"), supplier=_no_range_supplier(), history=history)
    assert "PATTERN_ANOMALY" not in flagged(inds)


def test_pattern_average_ignores_non_completed_payments():
    history = [txn("10000", n=i) for i in range(3)] + [txn("500000", status="FAILED", n=3)]
    assert "PATTERN_ANOMALY" in flagged(run(_pattern_invoice("35000"), supplier=_no_range_supplier(), history=history))


# --- urgency (POL-004) ---------------------------------------------------------------


def test_immediate_urgency_is_flagged():
    assert "URGENCY_INDICATOR" in flagged(run(invoice(urgency=Urgency.IMMEDIATE)))


def test_high_priority_is_not_flagged():
    # Reference behaviour: only IMMEDIATE fires POL-004. See ADR-014.
    assert "URGENCY_INDICATOR" not in flagged(run(invoice(urgency=Urgency.HIGH)))


# --- duplicates ------------------------------------------------------------------------


def _metro(id_, month, day, amount="18400"):
    return invoice(
        id=id_,
        supplier_id="SUP-002",
        supplier_name="Metro Cleaning Services",
        amount=Decimal(amount),
        date=dt.date(2026, month, day),
        bank_account="****7733",
        bank_account_holder="Metro Cleaning Services CC",
        supplier_email="billing@metrocleaning.co.za",
        line_items=[
            LineItem(
                description=f"Monthly cleaning service - {dt.date(2026, month, 1):%B} 2026", total=Decimal("12500")
            ),
            LineItem(description="Deep clean - boardroom", total=Decimal("3500")),
        ],
    )


def test_recurring_monthly_invoice_is_not_a_duplicate():
    # Same supplier, same amount, different month named in the line items, different date.
    aug, sep = _metro("INV-A", 8, 1), _metro("INV-B", 9, 1)
    assert "DUPLICATE_INVOICE" not in flagged(run(sep, supplier="SUP-002", others=[aug]))


def test_identical_line_items_are_a_duplicate():
    original = _metro("INV-A", 8, 1)
    resubmitted = _metro("INV-A2", 8, 1).model_copy(update={"date": dt.date(2026, 8, 20), "amount": Decimal("18500")})
    finding = next(i for i in run(resubmitted, supplier="SUP-002", others=[original]) if i.type == "DUPLICATE_INVOICE")
    assert "INV-A" in finding.description


def test_line_item_comparison_ignores_case_whitespace_and_order():
    original = _metro("INV-A", 8, 1)
    shuffled = original.model_copy(
        update={
            "id": "INV-A2",
            "date": dt.date(2026, 8, 5),
            "line_items": [
                LineItem(description="  DEEP clean -   boardroom "),
                LineItem(description="monthly cleaning service - august 2026"),
            ],
        }
    )
    assert "DUPLICATE_INVOICE" in flagged(run(shuffled, supplier="SUP-002", others=[original]))


def test_same_amount_same_date_is_a_duplicate():
    a = _metro("INV-A", 8, 1)
    b = a.model_copy(update={"id": "INV-B", "line_items": [LineItem(description="Cleaning services rendered")]})
    assert "DUPLICATE_INVOICE" in flagged(run(b, supplier="SUP-002", others=[a]))


def test_duplicate_must_be_same_supplier():
    a = _metro("INV-A", 8, 1).model_copy(update={"supplier_id": "SUP-001"})
    b = _metro("INV-B", 8, 1)
    assert "DUPLICATE_INVOICE" not in flagged(run(b, supplier="SUP-002", others=[a]))


def test_rejected_invoice_is_not_a_duplicate_source():
    a = _metro("INV-A", 8, 1).model_copy(update={"status": InvoiceStatus.REJECTED})
    b = _metro("INV-B", 8, 1)
    assert "DUPLICATE_INVOICE" not in flagged(run(b, supplier="SUP-002", others=[a]))


def test_invoice_is_not_a_duplicate_of_itself():
    a = _metro("INV-A", 8, 1)
    assert "DUPLICATE_INVOICE" not in flagged(run(a, supplier="SUP-002", others=[a]))


def test_empty_line_items_do_not_match_each_other():
    a = _metro("INV-A", 8, 1).model_copy(update={"line_items": []})
    b = _metro("INV-B", 8, 9, amount="1").model_copy(update={"line_items": []})
    assert "DUPLICATE_INVOICE" not in flagged(run(b, supplier="SUP-002", others=[a]))


# --- reference sample invoices ------------------------------------------------------------


def test_sample_inv_1048_abc_bank_change_and_urgency():
    inv = invoice(
        id="INV-1048",
        amount=Decimal("185000"),
        bank_account="1092847591 9917",
        bank_name="Capitec Bank",
        urgency=Urgency.IMMEDIATE,
    )
    assert flagged(run(inv)) == {"BANK_DETAILS_CHANGED", "UNUSUAL_AMOUNT", "URGENCY_INDICATOR"}


def test_sample_inv_1049_metro_is_clean():
    assert flagged(run(_metro("INV-1049", 8, 1), supplier="SUP-002")) == set()
