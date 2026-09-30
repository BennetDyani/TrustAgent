"""JSON invoices are already structured: map them in code, with no LLM.

They map into the same ``ExtractedInvoice`` shape, so JSON and LLM-read
documents go through one normaliser and one set of validation checks.
"""

import json
from typing import Any

from trustagent.extraction.documents import DocumentError
from trustagent.extraction.schema import ExtractedInvoice, ExtractedLineItem


def _s(value: Any) -> str | None:
    if value is None or value == "":
        return None
    return str(value)


def parse_json_invoice(text: str) -> ExtractedInvoice:
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise DocumentError(f"Invalid JSON: {exc.msg} (line {exc.lineno}).") from exc
    if not isinstance(data, dict):
        raise DocumentError("JSON invoice must be an object.")

    supplier = data.get("supplier") if isinstance(data.get("supplier"), dict) else {}
    bank = data.get("bank_details") if isinstance(data.get("bank_details"), dict) else {}
    items = data.get("line_items") if isinstance(data.get("line_items"), list) else []

    return ExtractedInvoice(
        invoice_number=_s(data.get("invoice_id") or data.get("invoice_number") or data.get("id")),
        supplier_name=_s(supplier.get("name") or data.get("supplier_name")),
        supplier_email=_s(supplier.get("contact") or supplier.get("email") or data.get("supplier_email")),
        total_due=_s(data.get("total") if data.get("total") is not None else data.get("amount")),
        subtotal=_s(data.get("subtotal")),
        vat_amount=_s(data.get("vat")),
        currency=_s(data.get("currency")),
        invoice_date=_s(data.get("invoice_date") or data.get("date")),
        due_date=_s(data.get("due_date")),
        priority=_s(data.get("urgency") or data.get("priority")),
        payment_terms=_s(data.get("payment_terms")),
        bank_name=_s(bank.get("bank_name") or data.get("bank_name")),
        bank_account_holder=_s(bank.get("account_holder") or data.get("bank_account_holder")),
        bank_account_number=_s(bank.get("account_number") or data.get("bank_account")),
        line_items=[
            ExtractedLineItem(
                description=str(li.get("description") or li.get("item") or ""),
                quantity=_s(li.get("quantity")),
                unit_price=_s(li.get("unit_price")),
                total=_s(li.get("total")),
            )
            for li in items
            if isinstance(li, dict)
        ],
        notes=_s(data.get("notes") or data.get("description")),
        warnings=[],
    )
