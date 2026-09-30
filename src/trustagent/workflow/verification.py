"""Supplier verification (POL-001), as pure rules.

Policy (ADR-029): when a supplier's bank account changes, payment is held and
the finance team must confirm the new account by phone AND by email, using
contact details from the ORIGINAL onboarding records. Contacts printed on the
invoice are exactly what a fraudster controls. Only then is the supplier
marked verified.

When a supplier is verified, each of its open cases is cleared only if the
invoice's bank account matches the now-verified record. A mismatch is logged
and never clears the case: the supplier may have confirmed its OLD account.
"""

from dataclasses import dataclass
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel

from trustagent.domain import Approver
from trustagent.rules.checks import last4, mask_account

ContactSource = Literal["onboarding_record", "invoice", "other"]


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
    if evidence.phone_source != "onboarding_record" or evidence.email_source != "onboarding_record":
        problems.append(
            "the phone number and email address must come from the original onboarding records, not from the invoice"
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
            f"both from onboarding records, by {verifier.name}.{notes}"
        ),
    )


class CaseVerificationOutcome(StrEnum):
    VERIFIED = "VERIFIED"
    BANK_MISMATCH = "BANK_MISMATCH"


def case_outcome_after_supplier_verified(invoice_account: str, verified_account: str) -> CaseVerificationOutcome:
    if last4(invoice_account) == last4(verified_account):
        return CaseVerificationOutcome.VERIFIED
    return CaseVerificationOutcome.BANK_MISMATCH
