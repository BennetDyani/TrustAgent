"""Compare an extracted Invoice with hand-labelled ground truth (data/invoices/labels.json).

Shared by the live tests, notebook 01 and the evaluation runner, so all three
score extraction the same way.
"""

import json
import re
from decimal import Decimal
from pathlib import Path
from typing import Any

from trustagent.config import get_settings
from trustagent.domain import Invoice
from trustagent.rules.checks import last4

FIELDS = (
    "invoice_number",
    "supplier_name",
    "amount",
    "currency",
    "invoice_date",
    "due_date",
    "account_last4",
    "bank_account_holder",
    "bank_name",
    "supplier_email",
    "urgency",
    "line_item_count",
)


def load_labels(path: Path | None = None) -> dict[str, dict[str, Any]]:
    path = path or get_settings().invoices_dir / "labels.json"
    return {k: v for k, v in json.loads(path.read_text(encoding="utf-8")).items() if not k.startswith("_")}


def _text(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip().casefold()


def actual_values(invoice: Invoice) -> dict[str, Any]:
    return {
        "invoice_number": invoice.id,
        "supplier_name": invoice.supplier_name,
        "amount": invoice.amount,
        "currency": invoice.currency,
        "invoice_date": invoice.date.isoformat() if invoice.date else None,
        "due_date": invoice.due_date.isoformat() if invoice.due_date else None,
        "account_last4": last4(invoice.bank_account),
        "bank_account_holder": invoice.bank_account_holder,
        "bank_name": invoice.bank_name,
        "supplier_email": invoice.supplier_email,
        "urgency": invoice.urgency.value,
        "line_item_count": len(invoice.line_items),
    }


def compare(invoice: Invoice | None, label: dict[str, Any]) -> dict[str, tuple[Any, Any, bool]]:
    """``{field: (expected, actual, ok)}``. Text compares case- and whitespace-insensitively; money exactly."""
    actual = actual_values(invoice) if invoice else dict.fromkeys(FIELDS)
    result = {}
    for name in FIELDS:
        expected, got = label.get(name), actual[name]
        if name == "amount":
            ok = got is not None and Decimal(str(expected)) == got
        elif name == "line_item_count":
            ok = expected == got
        else:
            ok = _text(expected) == _text(got)
        result[name] = (expected, got, ok)
    return result
