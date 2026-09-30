"""Supplier verification: phone AND email, then propagation to open cases (ADR-029)."""

import pytest

from trustagent.domain import Approver, Role
from trustagent.workflow.verification import (
    CaseVerificationOutcome,
    VerificationEvidence,
    VerificationRejected,
    case_outcome_after_supplier_verified,
    validate_verification,
)

ANALYST = Approver(name="Thandi Nkosi", role=Role.FINANCE_ANALYST)


def evidence(**overrides):
    data = dict(
        phone_confirmed=True,
        phone_contact="+27 11 803 4455",
        phone_source="onboarding_record",
        email_confirmed=True,
        email_contact="billing@metrocleaning.co.za",
        email_source="onboarding_record",
        confirmed_bank_account="04120398217733",
        notes="Spoke to J. Smith in accounts; confirmed by reply from the onboarding email address.",
    )
    data.update(overrides)
    return VerificationEvidence(**data)


def test_phone_and_email_confirmation_is_accepted():
    result = validate_verification(evidence(), ANALYST)
    assert result.bank_account == "****7733"  # only the masked form is kept


@pytest.mark.parametrize(
    "override, message",
    [
        ({"phone_confirmed": False}, "phone"),
        ({"email_confirmed": False}, "email"),
        ({"phone_confirmed": False, "email_confirmed": False}, "phone"),
        ({"phone_contact": " "}, "phone number"),
        ({"email_contact": ""}, "email address"),
        ({"confirmed_bank_account": "12"}, "account number"),
    ],
)
def test_verification_needs_both_channels_and_the_confirmed_account(override, message):
    with pytest.raises(VerificationRejected, match=message):
        validate_verification(evidence(**override), ANALYST)


@pytest.mark.parametrize("channel", ["phone_source", "email_source"])
@pytest.mark.parametrize("source", ["invoice", "other"])
def test_verifying_contacts_must_come_from_onboarding_records(channel, source):
    # Calling the number (or replying to the email) printed on a fraudulent invoice reaches the fraudster.
    with pytest.raises(VerificationRejected, match="onboarding"):
        validate_verification(evidence(**{channel: source}), ANALYST)


def test_case_is_verified_only_when_invoice_account_matches_the_verified_record():
    assert case_outcome_after_supplier_verified("****7733", "****7733") == CaseVerificationOutcome.VERIFIED
    assert case_outcome_after_supplier_verified("5501 9283 7488 01", "****8801") == CaseVerificationOutcome.VERIFIED


def test_mismatch_never_clears_the_case():
    # The supplier confirmed their OLD account: the invoice's new account is still unverified.
    assert case_outcome_after_supplier_verified("****9917", "****4821") == CaseVerificationOutcome.BANK_MISMATCH
