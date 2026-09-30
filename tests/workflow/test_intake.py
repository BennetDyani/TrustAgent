"""Intake against the test database, with a fake extractor."""

import pytest
from sqlalchemy import select

from tests.extraction.test_normalize import extracted
from trustagent.config import get_settings
from trustagent.db.models import AuditLogRow, InvestigationRow, InvoiceRow, SupplierRow
from trustagent.db.seed import seed
from trustagent.llm.factory import LLMUnavailable
from trustagent.workflow.intake import IntakeRejected, intake_document

INVOICES = get_settings().invoices_dir


@pytest.fixture
def db(db_session):
    seed(db_session)
    return db_session


def test_known_supplier_is_matched_and_a_pending_case_opened(db):
    result = intake_document(db, "inv.md", b"# invoice", "Thandi Nkosi", extractor=lambda _: extracted())
    assert result.supplier_match == "MATCHED_EXISTING" and result.supplier_id == "SUP-002"
    case = db.get(InvestigationRow, result.investigation_id)
    assert case.status == "PENDING" and case.invoice_id == "INV-1"
    row = db.get(InvoiceRow, "INV-1")
    assert row.bank_account == "****7733" and row.submitted_by == "Thandi Nkosi"


def test_unknown_supplier_is_registered_unverified(db):
    ex = extracted(supplier_name="Nexus Advisory Partners", supplier_email="k.mokoena.nexus@gmail.com",
                   bank_account_number="51007733829904", bank_name="TymeBank")  # fmt: skip
    result = intake_document(db, "nx.md", b"# invoice", "Thandi Nkosi", extractor=lambda _: ex)
    assert result.supplier_match == "NEW_SUPPLIER" and result.supplier_id == "SUP-004"
    supplier = db.get(SupplierRow, "SUP-004")
    assert not supplier.verified and supplier.bank_account == "****9904"
    actions = db.scalars(select(AuditLogRow.action).where(AuditLogRow.investigation_id == result.investigation_id))
    assert "New supplier registered as unverified" in list(actions)


def test_stored_text_never_contains_the_full_account_number(db):
    content = (INVOICES / "INV-1052-quickship-logistics.md").read_bytes()
    ex = extracted(supplier_name="QuickShip Logistics", bank_account_number="5501928374**8801**")
    intake_document(db, "INV-1052.md", content, "Thandi Nkosi", extractor=lambda _: ex)
    stored = db.get(InvoiceRow, "INV-1").raw_text
    assert "5501928374" not in stored and "****8801" in stored


def test_same_invoice_number_twice_is_rejected(db):
    intake_document(db, "a.md", b"# invoice", "Thandi Nkosi", extractor=lambda _: extracted())
    with pytest.raises(IntakeRejected) as exc:
        intake_document(db, "b.md", b"# invoice", "Thandi Nkosi", extractor=lambda _: extracted())
    assert exc.value.status_code == 409


def test_missing_critical_fields_are_rejected_and_nothing_stored(db):
    with pytest.raises(IntakeRejected) as exc:
        intake_document(db, "a.md", b"# invoice", "T", extractor=lambda _: extracted(bank_account_number=None))
    assert exc.value.status_code == 422 and "bank account number" in exc.value.message
    assert db.get(InvoiceRow, "INV-1") is None


def test_llm_outage_is_a_clean_503(db):
    def down(_):
        raise LLMUnavailable("quota")

    with pytest.raises(IntakeRejected) as exc:
        intake_document(db, "a.md", b"# invoice", "T", extractor=down)
    assert exc.value.status_code == 503 and "JSON" in exc.value.message


def test_ocr_needed_is_a_422(db):
    from tests.extraction.test_documents import _pdf

    with pytest.raises(IntakeRejected) as exc:
        intake_document(db, "scan.pdf", _pdf(None), "T")
    assert exc.value.status_code == 422 and "OCR" in exc.value.message


def test_json_upload_needs_no_llm(db):
    content = (INVOICES / "INV-1048-abc-office-solutions.json").read_bytes()
    result = intake_document(db, "INV-1048.json", content, "T", extractor=lambda _: pytest.fail("LLM called"))
    assert result.method == "json" and result.supplier_id == "SUP-001"


def test_duplicate_check_only_looks_at_earlier_uploads(db):
    """The first submission is the original; only the later copy is the duplicate (ADR-026)."""
    from trustagent.db.repository import load_check_context
    from trustagent.rules.checks import run_rule_checks

    original = extracted(invoice_number="INV-A")
    reissue = extracted(invoice_number="INV-B", invoice_date="15 September 2026", due_date="15 October 2026")
    intake_document(db, "a.md", b"# a", "T", extractor=lambda _: original)
    intake_document(db, "b.md", b"# b", "T", extractor=lambda _: reissue)

    def types(invoice_id):
        return {i.type for i in run_rule_checks(load_check_context(db, invoice_id))}

    assert "DUPLICATE_INVOICE" not in types("INV-A")
    assert "DUPLICATE_INVOICE" in types("INV-B")
