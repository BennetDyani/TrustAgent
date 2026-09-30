import datetime as dt
from decimal import Decimal

import pytest

from trustagent.domain import Urgency
from trustagent.extraction.normalize import (
    parse_amount,
    parse_date,
    redact_account_numbers,
    to_invoice,
    urgency_from_label,
)
from trustagent.extraction.schema import ExtractedInvoice, ExtractedLineItem


@pytest.mark.parametrize(
    "text, expected",
    [
        ("R 185,000.00", "185000.00"),
        ("R185000", "185000"),
        ("-R 187,500.00", "-187500.00"),
        ("R -500", "-500"),
        ("(R 1,200.00)", "-1200.00"),
        ("R 185 000,50", "185000.50"),  # space thousands, decimal comma
        ("R 1.234.567,89", "1234567.89"),
        ("5,000", "5000"),  # a quantity: comma is a thousands separator
        ("R 12,50", "12.50"),
        ("ZAR 98,500.00", "98500.00"),
        ("185000.0", "185000.0"),
        ("", None),
        ("N/A", None),
        (None, None),
    ],
)
def test_parse_amount(text, expected):
    assert parse_amount(text) == (Decimal(expected) if expected is not None else None)


@pytest.mark.parametrize(
    "text, expected",
    [
        ("19 August 2026", dt.date(2026, 8, 19)),
        ("1st September 2026", dt.date(2026, 9, 1)),
        ("2026-08-19", dt.date(2026, 8, 19)),
        ("01/08/2026", dt.date(2026, 8, 1)),  # day-first (South Africa)
        ("19 Aug 2026", dt.date(2026, 8, 19)),
        ("August 19, 2026", dt.date(2026, 8, 19)),
        ("next Tuesday", None),
        (None, None),
    ],
)
def test_parse_date(text, expected):
    assert parse_date(text) == expected


@pytest.mark.parametrize(
    "label, expected",
    [("URGENT", Urgency.IMMEDIATE), ("IMMEDIATE", Urgency.IMMEDIATE), ("Immediate payment", Urgency.IMMEDIATE),
     ("HIGH", Urgency.HIGH), ("Normal", Urgency.NORMAL), (None, Urgency.NORMAL)],
)  # fmt: skip
def test_urgency_from_label(label, expected):
    assert urgency_from_label(label) == expected


def extracted(**overrides) -> ExtractedInvoice:
    data = dict(
        invoice_number="INV-1", supplier_name="Metro Cleaning Services CC",
        supplier_email="billing@metrocleaning.co.za",
        total_due="R 18,400.00", subtotal="R 16,000.00", vat_amount="R 2,400.00", currency="ZAR",
        invoice_date="1 August 2026", due_date="30 August 2026", priority="Normal", payment_terms="30 Days",
        bank_name="Standard Bank", bank_account_holder="Metro Cleaning Services CC",
        bank_account_number="0412039821**7733**",
        line_items=[ExtractedLineItem(description="Monthly cleaning", quantity="1", unit_price="R 12,500.00",
                                      total="R 12,500.00"),
                    ExtractedLineItem(description="Deep clean", quantity="1", unit_price="R 3,500.00",
                                      total="R 3,500.00")],
        notes="As per contract MC-2025-004.", warnings=[],
    )  # fmt: skip
    data.update(overrides)
    return ExtractedInvoice(**data)


def test_clean_extraction_normalises_without_warnings():
    result = to_invoice(extracted(), fallback_id="file")
    inv = result.invoice
    assert result.warnings == [] and result.missing_critical == []
    assert inv.amount == Decimal("18400.00")
    assert inv.date == dt.date(2026, 8, 1)
    assert inv.urgency == Urgency.NORMAL
    assert [li.total for li in inv.line_items] == [Decimal("12500.00"), Decimal("3500.00")]


def test_last4_is_derived_in_code_and_only_the_mask_is_kept():
    # The model returns the account as printed; code picks the digits.
    inv = to_invoice(extracted(bank_account_number="5100 7733 8299 04"), fallback_id="f").invoice
    assert inv.bank_account == "****9904"  # not 7733, which also appears inside the number


@pytest.mark.parametrize(
    "override, missing",
    [
        ({"supplier_name": None}, "supplier name"),
        ({"supplier_name": "   "}, "supplier name"),
        ({"total_due": None}, "total amount"),
        ({"total_due": "see attached"}, "total amount"),
        ({"bank_account_number": None}, "bank account number"),
        ({"bank_account_number": "***"}, "bank account number"),
    ],
)
def test_missing_critical_field_blocks_the_invoice(override, missing):
    result = to_invoice(extracted(**override), fallback_id="f")
    assert result.invoice is None
    assert result.missing_critical == [missing]
    assert missing in result.warnings[0]


def test_arithmetic_mismatches_are_warnings():
    result = to_invoice(extracted(subtotal="R 16,500.00", total_due="R 19,000.00"), fallback_id="f")
    assert result.invoice is not None
    assert any("Line items add up to R16,000.00" in w for w in result.warnings)
    assert any("does not equal the total" in w for w in result.warnings)


def test_unparseable_date_and_due_before_invoice_date_are_warnings():
    w1 = to_invoice(extracted(invoice_date="sometime"), fallback_id="f").warnings
    assert any("Could not parse the invoice date" in w for w in w1)
    w2 = to_invoice(extracted(due_date="1 July 2026"), fallback_id="f").warnings
    assert any("before the invoice date" in w for w in w2)


def test_missing_invoice_number_uses_fallback_with_warning():
    result = to_invoice(extracted(invoice_number=None), fallback_id="upload-1")
    assert result.invoice.invoice_number == "upload-1"
    assert any("No invoice number" in w for w in result.warnings)


def test_currency_symbol_means_zar_and_foreign_currency_warns():
    assert to_invoice(extracted(currency="R"), fallback_id="f").invoice.currency == "ZAR"
    result = to_invoice(extracted(currency="USD"), fallback_id="f")
    assert result.invoice.currency == "USD" and any("USD" in w for w in result.warnings)


def test_line_total_computed_when_only_unit_price_given():
    items = [ExtractedLineItem(description="Paper", quantity="60", unit_price="R 289.00", total=None)]
    inv = to_invoice(extracted(line_items=items, subtotal=None, vat_amount=None), fallback_id="f").invoice
    assert inv.line_items[0].total == Decimal("17340.00")


@pytest.mark.parametrize(
    "text, number",
    [
        ("| Account Number | 1092847591**9917** |", "10928475919917"),
        ("Account Number\n51007733829904\n", "51007733829904"),
        ("Acc: 5100 7733 8299 04", "5100 7733 8299 04"),
    ],
)
def test_redaction_masks_the_full_account_number(text, number):
    redacted = redact_account_numbers(text, number)
    digits = "".join(c for c in number if c.isdigit())
    assert digits not in redacted.replace(" ", "").replace("*", "")
    assert f"****{digits[-4:]}" in redacted


def test_redaction_leaves_text_alone_when_number_is_already_masked():
    assert redact_account_numbers("Account ****8801", "****8801") == "Account ****8801"
