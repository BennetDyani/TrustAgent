"""Row <-> domain conversion and the few queries the workflow needs.

Kept thin: SQL lives here, business rules stay in rules/ and workflow/.
"""

import re

from sqlalchemy import select
from sqlalchemy.orm import Session

from trustagent.db.models import AuditLogRow, InvoiceRow, SupplierRow, TransactionRow
from trustagent.domain import Invoice, LineItem, Supplier, Transaction
from trustagent.rules.checks import CheckContext


def to_supplier(row: SupplierRow) -> Supplier:
    return Supplier.model_validate(row, from_attributes=True)


def to_transaction(row: TransactionRow) -> Transaction:
    return Transaction.model_validate(row, from_attributes=True)


def to_invoice(row: InvoiceRow) -> Invoice:
    return Invoice(
        id=row.id,
        invoice_number=row.invoice_number,
        supplier_id=row.supplier_id,
        supplier_name=row.supplier_name,
        amount=row.amount,
        currency=row.currency,
        date=row.date,
        due_date=row.due_date,
        bank_account=row.bank_account,
        bank_name=row.bank_name,
        bank_account_holder=row.bank_account_holder,
        supplier_email=row.supplier_email,
        description=row.description,
        line_items=[LineItem.model_validate(li) for li in row.line_items or []],
        status=row.status,
        urgency=row.urgency,
    )


def invoice_row_values(invoice: Invoice) -> dict:
    """Domain Invoice -> column values (line items as JSON-safe dicts)."""
    return {
        **invoice.model_dump(exclude={"line_items", "status", "urgency"}),
        "status": invoice.status.value,
        "urgency": invoice.urgency.value,
        "line_items": [li.model_dump(mode="json") for li in invoice.line_items],
    }


def list_suppliers(session: Session) -> list[Supplier]:
    return [to_supplier(r) for r in session.scalars(select(SupplierRow).order_by(SupplierRow.id))]


def next_supplier_id(session: Session) -> str:
    """Next SUP-NNN id. A concurrent insert of the same id fails on the primary key, loudly."""
    ids = session.scalars(select(SupplierRow.id).where(SupplierRow.id.like("SUP-%"))).all()
    numbers = [int(m.group(1)) for i in ids if (m := re.fullmatch(r"SUP-(\d+)", i))]
    return f"SUP-{max(numbers, default=0) + 1:03d}"


def load_check_context(session: Session, invoice_id: str) -> CheckContext:
    """Everything the rule checks need, loaded up front so the checks stay pure."""
    row = session.get(InvoiceRow, invoice_id)
    if row is None:
        raise LookupError(f"Invoice {invoice_id} not found")
    invoice = to_invoice(row)
    supplier = to_supplier(session.get(SupplierRow, row.supplier_id)) if row.supplier_id else None
    history, others = [], []
    if row.supplier_id:
        history = [
            to_transaction(t)
            for t in session.scalars(
                select(TransactionRow)
                .where(TransactionRow.supplier_id == row.supplier_id)
                .order_by(TransactionRow.date)
            )
        ]
        others = [
            to_invoice(r)
            for r in session.scalars(
                # Only earlier uploads: the first submission is the original (ADR-026).
                select(InvoiceRow).where(
                    InvoiceRow.supplier_id == row.supplier_id, InvoiceRow.upload_seq < row.upload_seq
                )
            )
        ]
    return CheckContext(invoice=invoice, supplier=supplier, history=history, other_invoices=others)


def append_audit(
    session: Session,
    investigation_id: str | None,
    actor: str,
    action: str,
    detail: str = "",
    tool_used: str | None = None,
    status: str = "COMPLETED",
) -> AuditLogRow:
    entry = AuditLogRow(
        investigation_id=investigation_id, actor=actor, action=action, detail=detail, tool_used=tool_used, status=status
    )
    session.add(entry)
    session.flush()
    return entry
