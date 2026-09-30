"""Domain types shared by the rules, graph, workflow and API.

Money is ``Decimal`` throughout: invoices are compared to the cent, and a
float can't represent R0.10 exactly.
"""

import datetime as dt
from decimal import Decimal
from enum import IntEnum, StrEnum

from pydantic import BaseModel, Field


class InvestigationStatus(StrEnum):
    PENDING = "PENDING"
    IN_PROGRESS = "IN_PROGRESS"
    ACTION_REQUIRED = "ACTION_REQUIRED"
    CLOSED = "CLOSED"
    FAILED = "FAILED"


class RiskLevel(StrEnum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"


class Action(StrEnum):
    APPROVE_PAYMENT = "APPROVE_PAYMENT"
    REQUEST_VERIFICATION = "REQUEST_VERIFICATION"
    ESCALATE = "ESCALATE"
    HOLD_PAYMENT = "HOLD_PAYMENT"


class HumanAction(StrEnum):
    """What a person can do on a case. The model can only recommend ``Action`` values;
    REJECT_INVOICE (close as fraud/not payable) is human-only (ADR-030)."""

    APPROVE_PAYMENT = "APPROVE_PAYMENT"
    REQUEST_VERIFICATION = "REQUEST_VERIFICATION"
    ESCALATE = "ESCALATE"
    HOLD_PAYMENT = "HOLD_PAYMENT"
    REJECT_INVOICE = "REJECT_INVOICE"


class Caution(IntEnum):
    """Caution order used by the minimum-action rule. Higher is more cautious."""

    APPROVE_PAYMENT = 0
    REQUEST_VERIFICATION = 1
    ESCALATE = 2
    HOLD_PAYMENT = 3


class Severity(StrEnum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"


class Urgency(StrEnum):
    NORMAL = "NORMAL"
    HIGH = "HIGH"
    IMMEDIATE = "IMMEDIATE"


class InvoiceStatus(StrEnum):
    SUBMITTED = "SUBMITTED"
    UNDER_REVIEW = "UNDER_REVIEW"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    ON_HOLD = "ON_HOLD"


class TransactionStatus(StrEnum):
    COMPLETED = "COMPLETED"
    PENDING = "PENDING"
    FAILED = "FAILED"
    ON_HOLD = "ON_HOLD"


class Role(StrEnum):
    FINANCE_ANALYST = "FINANCE_ANALYST"
    FINANCE_MANAGER = "FINANCE_MANAGER"
    DEPARTMENT_HEAD = "DEPARTMENT_HEAD"


class IndicatorSource(StrEnum):
    RULE = "RULE"  # deterministic Python check
    AI = "AI"  # model observation, restricted to AI_INDICATOR_TYPES
    CONTRACT = "CONTRACT"  # code comparison against RAG-retrieved contract terms


# --- Entities ----------------------------------------------------------------


class LineItem(BaseModel):
    description: str
    quantity: Decimal = Decimal("1")
    unit_price: Decimal = Decimal("0")
    total: Decimal = Decimal("0")


class Supplier(BaseModel):
    id: str
    name: str
    contact_email: str | None = None
    # Only the masked last 4 digits are ever stored (POPIA).
    bank_account: str
    bank_name: str | None = None
    registration_number: str | None = None
    risk_status: str = "LOW"
    verified: bool = False
    verified_date: dt.date | None = None
    verified_by: str | None = None
    expected_spend_min: Decimal | None = None
    expected_spend_max: Decimal | None = None


class Transaction(BaseModel):
    id: str
    supplier_id: str
    invoice_id: str | None = None
    amount: Decimal
    currency: str = "ZAR"
    bank_account: str
    date: dt.date
    status: TransactionStatus
    description: str = ""


class Invoice(BaseModel):
    # Internal id (database key). ``invoice_number`` is the supplier's own number: it is only
    # unique per supplier, so two suppliers may both send "INV-0001" (weak spot 3, ADR-045).
    id: str
    invoice_number: str = ""
    supplier_id: str | None = None
    supplier_name: str
    amount: Decimal
    currency: str = "ZAR"
    date: dt.date | None = None
    due_date: dt.date | None = None
    bank_account: str  # masked, e.g. "****4821"
    bank_name: str | None = None
    bank_account_holder: str | None = None
    supplier_email: str | None = None
    description: str = ""
    line_items: list[LineItem] = Field(default_factory=list)
    status: InvoiceStatus = InvoiceStatus.SUBMITTED
    urgency: Urgency = Urgency.NORMAL
    # Every bank account number found in the document (masked), by a code scanner (ADR-046).
    document_accounts: list[str] = Field(default_factory=list)

    def model_post_init(self, _context: object) -> None:
        if not self.invoice_number:
            self.invoice_number = self.id


class Approver(BaseModel):
    name: str
    role: Role


class Approval(Approver):
    approved_at: dt.datetime


class Citation(BaseModel):
    source: str
    page: int | None = None
    section: str | None = None


class RiskIndicator(BaseModel):
    type: str
    description: str
    severity: Severity
    source: IndicatorSource = IndicatorSource.RULE
    citations: list[Citation] = Field(default_factory=list)
    # For AI observations: the rule finding the model says this overlaps (ADR-042).
    overlaps: str | None = None


class ScoredIndicator(RiskIndicator):
    weight: int


class RiskResult(BaseModel):
    score: int
    level: RiskLevel
    indicators: list[ScoredIndicator]
