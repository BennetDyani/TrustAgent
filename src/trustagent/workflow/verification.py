"""Supplier verification (POL-001), as pure rules.

Policy (ADR-029): when a supplier's bank account changes, payment is held and
the finance team must confirm the new account by phone AND by email, using
contact details from the ORIGINAL onboarding records (for a brand-new supplier,
another independent source such as the company register). Contacts printed on
the invoice are exactly what a fraudster controls. Only then is the supplier
marked verified.

When a supplier is verified, each of its open cases is cleared only if the
invoice's bank account matches the now-verified record. A mismatch is logged
and never clears the case: the supplier may have confirmed its OLD account.
"""

import datetime as dt
from dataclasses import dataclass
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from trustagent.domain import Approver
from trustagent.rules.checks import last4, mask_account

# Where the phone number / email address used for verification came from. Contacts printed
# on the invoice are exactly what a fraudster controls, so they are never accepted.
ContactSource = Literal["onboarding_record", "independent_source", "invoice", "other"]
TRUSTED_SOURCES = frozenset({"onboarding_record", "independent_source"})


class VerificationRejected(ValueError):
    """The verification record doesn't meet POL-001 (HTTP 422)."""


class VerificationEvidence(BaseModel):
    phone_confirmed: bool
    phone_contact: str
    phone_source: ContactSource
    email_confirmed: bool
    email_contact: str
    email_source: ContactSource
    # The account number the supplier confirmed on the call/email. Only the masked form is kept.
    confirmed_bank_account: str
    notes: str = ""


@dataclass(frozen=True)
class ValidatedVerification:
    bank_account: str  # masked
    verified_by: Approver
    summary: str


def validate_verification(evidence: VerificationEvidence, verifier: Approver) -> ValidatedVerification:
    problems = []
    if not evidence.phone_confirmed:
        problems.append("the new bank account has not been confirmed by phone")
    if not evidence.email_confirmed:
        problems.append("the new bank account has not been confirmed by email")
    if not evidence.phone_contact.strip():
        problems.append("record the phone number that was called")
    if not evidence.email_contact.strip():
        problems.append("record the email address that was used")
    if evidence.phone_source not in TRUSTED_SOURCES or evidence.email_source not in TRUSTED_SOURCES:
        problems.append(
            "the phone number and email address must come from the original onboarding records (or, for a new "
            "supplier, another independent source such as the company register), never from the invoice"
        )
    if len(last4(evidence.confirmed_bank_account)) < 4:
        problems.append("record the account number the supplier confirmed")
    if problems:
        raise VerificationRejected("Cannot mark the supplier verified: " + "; ".join(problems) + ".")

    account = mask_account(evidence.confirmed_bank_account)
    notes = f" Notes: {evidence.notes.strip()}" if evidence.notes.strip() else ""
    return ValidatedVerification(
        bank_account=account,
        verified_by=verifier,
        summary=(
            f"Account {account} confirmed by phone ({evidence.phone_contact}) and email ({evidence.email_contact}), "
            f"both from trusted sources, by {verifier.name}.{notes}"
        ),
    )


class CaseVerificationOutcome(StrEnum):
    VERIFIED = "VERIFIED"
    BANK_MISMATCH = "BANK_MISMATCH"


def case_outcome_after_supplier_verified(invoice_account: str, verified_account: str) -> CaseVerificationOutcome:
    if last4(invoice_account) == last4(verified_account):
        return CaseVerificationOutcome.VERIFIED
    return CaseVerificationOutcome.BANK_MISMATCH


# --- applying a verification (database) -------------------------------------------------


@dataclass
class SupplierVerificationOutcome:
    supplier_id: str
    cases_verified: list[str]
    cases_mismatched: list[str]


def apply_supplier_verification(
    session: Session, supplier_id: str, evidence: VerificationEvidence, verifier: Approver, today: dt.date
) -> SupplierVerificationOutcome:
    """Mark the supplier verified with the confirmed account, then update its open cases.

    Raises ``VerificationRejected`` if the evidence doesn't meet POL-001.
    """
    from trustagent.db import repository as repo
    from trustagent.db.models import InvestigationRow, InvoiceRow, SupplierRow

    validated = validate_verification(evidence, verifier)
    supplier = session.get(SupplierRow, supplier_id, with_for_update=True)
    if supplier is None:
        raise LookupError(f"Supplier {supplier_id} not found.")
    previous = supplier.bank_account
    supplier.bank_account = validated.bank_account
    supplier.verified, supplier.verified_by, supplier.verified_date = True, verifier.name, today
    from trustagent.workflow.approvals import ROLE_LABELS

    who = f"{verifier.name} ({ROLE_LABELS[verifier.role]})"

    verified, mismatched = [], []
    cases = session.scalars(
        select(InvestigationRow)
        .where(InvestigationRow.supplier_id == supplier_id, InvestigationRow.status == "ACTION_REQUIRED")
        .with_for_update()
    ).all()
    for case in cases:
        invoice = session.get(InvoiceRow, case.invoice_id)
        outcome = case_outcome_after_supplier_verified(invoice.bank_account, validated.bank_account)
        if outcome == CaseVerificationOutcome.BANK_MISMATCH:
            repo.append_audit(session, case.id, who, "Supplier verified - bank details still differ",
                              f"{supplier.name} was verified with account {validated.bank_account}, but this invoice "
                              f"pays {invoice.bank_account}. Verification NOT applied; "
                              "the case stays on hold.")  # fmt: skip
            mismatched.append(case.id)
            continue
        current = case.verification or {}
        case.verification = {
            "status": "VERIFIED",
            "requested_by": current.get("requested_by"),
            "requested_at": current.get("requested_at"),
            "verified_by": who,
            "verified_by_name": verifier.name,  # separation of duties (ADR-048)
            "verified_at": dt.datetime.now(dt.UTC).isoformat(),
            "verified_during_run": case.run_number,  # approval needs a re-run after this (ADR-048)
        }
        repo.append_audit(session, case.id, who, "Supplier verified", validated.summary)
        verified.append(case.id)

    repo.append_audit(session, None, who, f"Supplier {supplier_id} verified",
                      f"{validated.summary} Previous account on file: {previous}.")  # fmt: skip
    session.flush()
    return SupplierVerificationOutcome(supplier_id, verified, mismatched)
