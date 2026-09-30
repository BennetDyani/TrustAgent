"""Intake against the test database, with a fake extractor."""

import pytest
from sqlalchemy import select

from tests.extraction.test_normalize import extracted
from tests.factories import invoice_document
from trustagent.config import get_settings
from trustagent.db.models import AuditLogRow, InvestigationRow, InvoiceRow, SupplierRow
from trustagent.db.seed import seed
from trustagent.llm.factory import LLMUnavailable
from trustagent.workflow.intake import IntakeRejected, intake_document

INVOICES = get_settings().invoices_dir


def upload(db, ex, filename="inv.md", by="Thandi Nkosi"):
    return intake_document(db, filename, invoice_document(ex).encode(), by, extractor=lambda _: ex)


@pytest.fixture
def db(db_session):
    seed(db_session)
    return db_session


def by_number(db, number: str) -> InvoiceRow | None:
    return db.scalar(select(InvoiceRow).where(InvoiceRow.invoice_number == number))


def test_known_supplier_is_matched_and_a_pending_case_opened(db):
    result = upload(db, extracted(), "inv.md", "Thandi Nkosi")
    assert result.supplier_match == "MATCHED_EXISTING" and result.supplier_id == "SUP-002"
    case = db.get(InvestigationRow, result.investigation_id)
    row = by_number(db, "INV-1")
    assert case.status == "PENDING" and case.invoice_id == row.id == result.invoice.id
    assert row.id != "INV-1"  # internal id, not the supplier's invoice number
    assert row.bank_account == "****7733" and row.submitted_by == "Thandi Nkosi"


def test_unknown_supplier_is_registered_unverified(db):
    ex = extracted(supplier_name="Nexus Advisory Partners", supplier_email="k.mokoena.nexus@gmail.com",
                   bank_account_number="51007733829904", bank_name="TymeBank")  # fmt: skip
    result = upload(db, ex, "nx.md", "Thandi Nkosi")
    assert result.supplier_match == "NEW_SUPPLIER" and result.supplier_id == "SUP-004"
    supplier = db.get(SupplierRow, "SUP-004")
    assert not supplier.verified and supplier.bank_account == "****9904"
    actions = db.scalars(select(AuditLogRow.action).where(AuditLogRow.investigation_id == result.investigation_id))
    assert "New supplier registered as unverified" in list(actions)


def test_stored_text_never_contains_the_full_account_number(db):
    content = (INVOICES / "INV-1052-quickship-logistics.md").read_bytes()
    ex = extracted(
        supplier_name="QuickShip Logistics",
        bank_account_number="5501928374**8801**",
        total_due="R 126,500.00",
        subtotal=None,
        vat_amount=None,
    )
    intake_document(db, "INV-1052.md", content, "Thandi Nkosi", extractor=lambda _: ex)
    stored = by_number(db, "INV-1").raw_text
    assert "5501928374" not in stored and "****8801" in stored


def test_same_invoice_number_from_the_same_supplier_is_rejected_and_audited(db):
    upload(db, extracted(), "a.md", "Thandi Nkosi")
    with pytest.raises(IntakeRejected) as exc:
        upload(db, extracted(), "b.md", "Thandi Nkosi")
    assert exc.value.status_code == 409 and "SUP-002" in exc.value.message
    assert "Upload rejected: invoice number already on file" in list(db.scalars(select(AuditLogRow.action)))


def test_two_suppliers_may_use_the_same_invoice_number(db):
    """Invoice numbers are only unique per supplier (weak spot 3)."""
    a = upload(db, extracted(invoice_number="0001"), "a.md", "T")
    abc = extracted(invoice_number="0001", supplier_name="ABC Office Solutions", bank_account_number="62718304554821")
    b = upload(db, abc, "b.md", "T")
    assert (a.supplier_id, b.supplier_id) == ("SUP-002", "SUP-001")
    assert a.invoice.id != b.invoice.id and a.invoice.invoice_number == b.invoice.invoice_number == "0001"


def test_missing_critical_fields_are_rejected_and_nothing_stored(db):
    with pytest.raises(IntakeRejected) as exc:
        upload(db, extracted(bank_account_number=None), "a.md", "T")
    assert exc.value.status_code == 422 and "bank account number" in exc.value.message
    assert by_number(db, "INV-1") is None


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
    a = upload(db, original, "a.md", "T").invoice.id
    b = upload(db, reissue, "b.md", "T").invoice.id

    def findings(invoice_id):
        return {i.type: i for i in run_rule_checks(load_check_context(db, invoice_id))}

    assert "DUPLICATE_INVOICE" not in findings(a)
    assert "INV-A" in findings(b)["DUPLICATE_INVOICE"].description  # humans see the invoice number
