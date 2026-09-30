"""Parse the model's as-printed strings into typed values, and validate the result.

Pure functions: this is where "derive in code, not in the model" happens.
"""

import datetime as dt
import re
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation

from trustagent.domain import Invoice, LineItem, Urgency
from trustagent.extraction.schema import ExtractedInvoice
from trustagent.rules.checks import mask_account

# Day-first formats: South African invoices write 01/08/2026 for 1 August.
_DATE_FORMATS = ("%Y-%m-%d", "%d %B %Y", "%d %b %Y", "%d/%m/%Y", "%Y/%m/%d", "%d-%m-%Y", "%B %d, %Y", "%b %d, %Y")

CRITICAL_FIELDS = ("supplier name", "total amount", "bank account number")


def parse_amount(text: str | None) -> Decimal | None:
    """'R 185,000.00' -> 185000.00; '-R 187,500.00' -> -187500.00; 'R185 000,50' -> 185000.50."""
    if text is None:
        return None
    s = text.strip()
    negative = bool(re.match(r"^[^\d]*[-(]", s))  # "-R 500", "R -500", "(R 500)"
    s = re.sub(r"[^\d.,]", "", s)
    if not s or not re.search(r"\d", s):
        return None
    if "," in s and "." in s:
        # Whichever separator comes last is the decimal point.
        s = s.replace(",", "") if s.rfind(".") > s.rfind(",") else s.replace(".", "").replace(",", ".")
    elif "," in s:
        # A comma followed by exactly two final digits is a decimal comma; otherwise thousands.
        s = s.replace(",", ".") if re.search(r",\d{2}$", s) and s.count(",") == 1 else s.replace(",", "")
    try:
        value = Decimal(s)
    except InvalidOperation:
        return None
    return -value if negative else value


def parse_date(text: str | None) -> dt.date | None:
    if not text:
        return None
    s = re.sub(r"\s+", " ", text.strip())
    s = re.sub(r"(\d)(st|nd|rd|th)\b", r"\1", s)  # "1st August 2026"
    for fmt in _DATE_FORMATS:
        try:
            return dt.datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    return None


def urgency_from_label(label: str | None) -> Urgency:
    """POL-004 reads the *priority label*. URGENT counts as IMMEDIATE (ADR-014)."""
    text = (label or "").upper()
    if "IMMEDIATE" in text or "URGENT" in text:
        return Urgency.IMMEDIATE
    if "HIGH" in text:
        return Urgency.HIGH
    return Urgency.NORMAL


def currency_code(text: str | None) -> str:
    t = (text or "").strip().upper()
    return "ZAR" if t in ("", "R", "ZAR", "RAND", "RANDS") else t


@dataclass
class NormalizedInvoice:
    invoice: Invoice | None
    warnings: list[str] = field(default_factory=list)
    missing_critical: list[str] = field(default_factory=list)


def _cents(a: Decimal, b: Decimal) -> bool:
    return abs(a - b) <= Decimal("0.01")


def to_invoice(ex: ExtractedInvoice, *, fallback_id: str) -> NormalizedInvoice:
    """Build a domain Invoice from as-printed values, collecting validation warnings.

    ``invoice`` is None when a critical field is missing: an invoice with no
    amount or account can't be checked, and must not become a silent R0 case.
    """
    warnings: list[str] = []
    missing: list[str] = []

    amount = parse_amount(ex.total_due)
    account_digits = re.sub(r"\D", "", ex.bank_account_number or "")
    if not (ex.supplier_name or "").strip():
        missing.append("supplier name")
    if amount is None:
        missing.append("total amount")
    if len(account_digits) < 4:
        missing.append("bank account number")
    if missing:
        return NormalizedInvoice(None, [f"Could not read: {', '.join(missing)}."], missing)

    invoice_date, due_date = parse_date(ex.invoice_date), parse_date(ex.due_date)
    for label, raw, parsed in (("invoice date", ex.invoice_date, invoice_date), ("due date", ex.due_date, due_date)):
        if raw and parsed is None:
            warnings.append(f"Could not parse the {label} '{raw}'.")
    if invoice_date and due_date and due_date < invoice_date:
        warnings.append(f"Due date {due_date} is before the invoice date {invoice_date}.")

    items: list[LineItem] = []
    for li in ex.line_items:
        qty = parse_amount(li.quantity) or Decimal(1)
        unit = parse_amount(li.unit_price)
        total = parse_amount(li.total)
        if total is None and unit is not None:
            total = qty * unit
        items.append(LineItem(description=li.description.strip(), quantity=qty, unit_price=unit or Decimal(0),
                              total=total if total is not None else Decimal(0)))  # fmt: skip

    # Arithmetic cross-checks: recorded as validation warnings for the reviewer,
    # and available to the AI review as possible DOCUMENT_ANOMALY context.
    subtotal, vat = parse_amount(ex.subtotal), parse_amount(ex.vat_amount)
    if items and subtotal is not None:
        line_sum = sum((li.total for li in items), Decimal(0))
        if not _cents(line_sum, subtotal):
            warnings.append(f"Line items add up to R{line_sum:,.2f} but the subtotal says R{subtotal:,.2f}.")
    if subtotal is not None and vat is not None and not _cents(subtotal + vat, amount):
        warnings.append(f"Subtotal R{subtotal:,.2f} + VAT R{vat:,.2f} does not equal the total R{amount:,.2f}.")

    currency = currency_code(ex.currency)
    if currency != "ZAR":
        warnings.append(f"Invoice currency is {currency}; thresholds and ranges are in ZAR.")

    number = (ex.invoice_number or "").strip()
    if not number:
        number = fallback_id
        warnings.append(f"No invoice number found; using '{fallback_id}'.")

    invoice = Invoice(
        id=number,  # provisional; intake assigns the internal id
        invoice_number=number,
        supplier_name=ex.supplier_name.strip(),
        amount=amount,
        currency=currency,
        date=invoice_date,
        due_date=due_date,
        bank_account=mask_account(account_digits),  # only the masked form leaves this function
        bank_name=(ex.bank_name or "").strip() or None,
        bank_account_holder=(ex.bank_account_holder or "").strip() or None,
        supplier_email=(ex.supplier_email or "").strip() or None,
        description=(ex.notes or "").strip()[:1000],
        line_items=items,
        urgency=urgency_from_label(ex.priority),
    )
    return NormalizedInvoice(invoice, warnings, [])


def redact_account_numbers(text: str, account_number: str | None) -> str:
    """Replace the full account number in stored/forwarded text with its masked form (POPIA).

    Matches the digits even when printed with spaces, dashes or markdown bold
    (``1092847591**9917**``).
    """
    digits = re.sub(r"\D", "", account_number or "")
    if len(digits) < 5:
        return text
    pattern = r"[\s*\-]*".join(re.escape(d) for d in digits)
    return re.sub(pattern, mask_account(digits), text)
