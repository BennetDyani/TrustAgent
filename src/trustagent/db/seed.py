"""Seed suppliers, transactions and policies, copied from the reference implementation.

Idempotent: rows that already exist are left alone, so re-running the seed
never overwrites a supplier someone has since verified or edited.

Run with:  uv run python -m trustagent.db.seed
"""

from datetime import date
from decimal import Decimal

from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from trustagent.db.models import PolicyRow, SupplierRow, TransactionRow
from trustagent.db.session import session_scope

SUPPLIERS: list[dict] = [
    {
        "id": "SUP-001",
        "name": "ABC Office Solutions",
        "contact_email": "accounts@abcoffice.co.za",
        "bank_account": "****4821",
        "bank_name": "First National Bank",
        "registration_number": "2019/123456/07",
        "risk_status": "LOW",
        "verified": True,
        "verified_date": date(2026, 2, 15),
        "verified_by": "finance@company.co.za",
        "expected_spend_min": Decimal("15000"),
        "expected_spend_max": Decimal("40000"),
    },
    {
        "id": "SUP-002",
        "name": "Metro Cleaning Services",
        "contact_email": "billing@metrocleaning.co.za",
        "bank_account": "****7733",
        "bank_name": "Standard Bank",
        "registration_number": "2020/654321/07",
        "risk_status": "LOW",
        "verified": True,
        "verified_date": date(2026, 5, 20),
        "verified_by": "finance@company.co.za",
        "expected_spend_min": Decimal("10000"),
        "expected_spend_max": Decimal("20000"),
    },
    {
        "id": "SUP-003",
        "name": "Digital Print Co",
        "contact_email": "invoices@digitalprint.co.za",
        "bank_account": "****2190",
        "bank_name": "Absa Bank",
        "registration_number": "2018/998877/07",
        "risk_status": "MEDIUM",
        "verified": True,
        "verified_date": date(2025, 11, 10),
        "verified_by": "finance@company.co.za",
        "expected_spend_min": None,
        "expected_spend_max": None,
    },
]


def _txn(id_, supplier, invoice, amount, account, day, description):
    return {
        "id": id_,
        "supplier_id": supplier,
        "invoice_id": invoice,
        "amount": Decimal(amount),
        "currency": "ZAR",
        "bank_account": account,
        "date": day,
        "status": "COMPLETED",
        "description": description,
    }


TRANSACTIONS: list[dict] = [
    # ABC Office Solutions: consistent bank account ****4821
    _txn("TXN-001", "SUP-001", "INV-1041", "22500", "****4821", date(2026, 3, 15), "Office supplies - Q1 2026"),
    _txn("TXN-002", "SUP-001", "INV-1043", "18750", "****4821", date(2026, 4, 22), "Printer cartridges and paper"),
    _txn("TXN-003", "SUP-001", "INV-1044", "31200", "****4821", date(2026, 5, 10), "Office furniture - 5 desks"),
    _txn("TXN-004", "SUP-001", "INV-1045", "27800", "****4821", date(2026, 6, 18), "IT peripherals and accessories"),
    _txn("TXN-005", "SUP-001", "INV-1046", "24300", "****4821", date(2026, 7, 5), "Monthly office supplies"),
    # Metro Cleaning Services
    _txn("TXN-006", "SUP-002", "INV-1047", "15000", "****7733", date(2026, 7, 1), "Monthly cleaning - July"),
]

POLICIES: list[dict] = [
    {
        "id": "POL-001",
        "name": "Supplier Bank Account Change Verification",
        "category": "SUPPLIER",
        "rule": "Any change to supplier banking details must be independently verified via phone call "
        "to the supplier using contact details from original onboarding records before processing payment.",
        "description": "When a supplier changes their banking details, the change must be verified "
        "independently before any payments are processed to the new account.",
        "severity": "CRITICAL",
        "action": "HOLD payment and verify banking details independently before processing.",
    },
    {
        "id": "POL-002",
        "name": "Large Transaction Threshold",
        "category": "PAYMENT",
        "rule": "Transactions exceeding R100,000 require dual authorization from Finance Manager and Department Head.",
        "description": "Any single payment above R100,000 must be approved by both the Finance Manager "
        "and the relevant Department Head.",
        "severity": "HIGH",
        "action": "Escalate for dual authorization before processing.",
    },
    {
        "id": "POL-003",
        "name": "Supplier Transaction Pattern Monitoring",
        "category": "COMPLIANCE",
        "rule": "Any invoice exceeding 3x the supplier historical average must be flagged for review.",
        "description": "Transactions that significantly deviate from established patterns must be "
        "reviewed before processing.",
        "severity": "HIGH",
        "action": "Flag for investigation and review before payment.",
    },
    {
        "id": "POL-004",
        "name": "Urgent Payment Request Protocol",
        "category": "PAYMENT",
        "rule": "Payment requests marked as IMMEDIATE or urgent must undergo additional scrutiny as this "
        "is a common social engineering tactic.",
        "description": "Urgency indicators in payment requests are a known fraud signal and require "
        "additional verification.",
        "severity": "MEDIUM",
        "action": "Apply additional verification steps before processing urgent requests.",
    },
]


def seed(session: Session) -> dict[str, int]:
    """Insert seed rows that don't exist yet. Returns how many were inserted per table."""
    inserted = {}
    # Suppliers first: transactions reference them.
    for model, rows in ((SupplierRow, SUPPLIERS), (TransactionRow, TRANSACTIONS), (PolicyRow, POLICIES)):
        stmt = insert(model).values(rows).on_conflict_do_nothing(index_elements=["id"]).returning(model.id)
        # RETURNING lists only the rows actually inserted (rowcount is -1 here).
        inserted[model.__tablename__] = len(session.execute(stmt).all())
    return inserted


def main() -> None:
    with session_scope() as session:
        counts = seed(session)
    print("Seeded:", ", ".join(f"{table}={n}" for table, n in counts.items()))


if __name__ == "__main__":
    main()
