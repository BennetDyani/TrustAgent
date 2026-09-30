"""Small builders for domain objects, so each test states only what it's about."""

import datetime as dt
from decimal import Decimal

from trustagent.db.seed import SUPPLIERS, TRANSACTIONS
from trustagent.domain import Invoice, LineItem, Supplier, Transaction, Urgency


def seed_supplier(supplier_id: str, **overrides) -> Supplier:
    data = next(s for s in SUPPLIERS if s["id"] == supplier_id)
    return Supplier(**{**data, **overrides})


def seed_history(supplier_id: str) -> list[Transaction]:
    return [Transaction(**t) for t in TRANSACTIONS if t["supplier_id"] == supplier_id]


def invoice(**overrides) -> Invoice:
    """A clean ABC Office Solutions invoice; override fields to create a finding."""
    data = {
        "id": "INV-T001",
        "supplier_id": "SUP-001",
        "supplier_name": "ABC Office Solutions",
        "amount": Decimal("25000"),
        "date": dt.date(2026, 8, 1),
        "due_date": dt.date(2026, 8, 31),
        "bank_account": "****4821",
        "bank_name": "First National Bank",
        "bank_account_holder": "ABC Office Solutions (Pty) Ltd",
        "supplier_email": "accounts@abcoffice.co.za",
        "description": "Office supplies",
        "line_items": [LineItem(description="Office supplies - August 2026", total=Decimal("25000"))],
        "urgency": Urgency.NORMAL,
    }
    data.update(overrides)
    return Invoice(**data)


def txn(amount: str, supplier_id: str = "SUP-X", status: str = "COMPLETED", n: int = 0) -> Transaction:
    return Transaction(
        id=f"TXN-T{n:03d}",
        supplier_id=supplier_id,
        amount=Decimal(amount),
        bank_account="****0000",
        date=dt.date(2026, 1, 1) + dt.timedelta(days=30 * n),
        status=status,
    )


def invoice_document(ex, extra: str = "") -> str:
    """Render an ExtractedInvoice as a small Markdown invoice that really contains its values.

    Extraction is grounded in the document (ADR-046), so fake extractions need a document that
    matches them, just as a real one would.
    """
    lines = [
        f"# INVOICE {ex.invoice_number or ''}",
        f"From: {ex.supplier_name or ''}",
        f"Email: {ex.supplier_email or ''}",
        f"Date: {ex.invoice_date or ''}   Due: {ex.due_date or ''}   Priority: {ex.priority or ''}",
        f"| Bank | {ex.bank_name or ''} |",
        f"| Account Holder | {ex.bank_account_holder or ''} |",
        f"| Account Number | {ex.bank_account_number or ''} |",
        *(f"| {li.description} | {li.quantity} | {li.unit_price} | {li.total} |" for li in ex.line_items),
        f"Subtotal: {ex.subtotal or ''}   VAT: {ex.vat_amount or ''}",
        f"Total Due: {ex.total_due or ''}",
        ex.notes or "",
        extra,
    ]
    return "\n".join(lines)
