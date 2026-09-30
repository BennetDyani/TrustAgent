"""Upload -> extract -> validate -> match supplier -> store invoice + PENDING case.

Extraction happens here, at intake, not inside the investigation graph
(ADR-023). The reviewer sees the extracted fields and warnings before
investigating, and a failed extraction is a clean upload error, never a
half-finished case.
"""

import secrets
from dataclasses import dataclass, field
from typing import Literal

from sqlalchemy import select
from sqlalchemy.orm import Session

from trustagent.config import get_settings
from trustagent.db import repository as repo
from trustagent.db.models import InvestigationRow, InvoiceRow, SupplierRow
from trustagent.domain import Invoice
from trustagent.extraction.documents import DocumentError
from trustagent.extraction.pipeline import ExtractionResult, Extractor, extract_document
from trustagent.llm.factory import LLMUnavailable
from trustagent.rules.matching import find_best_supplier


class IntakeRejected(Exception):
    def __init__(self, status_code: int, message: str):
        super().__init__(message)
        self.status_code = status_code
        self.message = message


@dataclass
class IntakeResult:
    investigation_id: str
    invoice: Invoice
    supplier_id: str
    supplier_match: Literal["MATCHED_EXISTING", "NEW_SUPPLIER"]
    match_confidence: float
    method: str
    warnings: list[str] = field(default_factory=list)


def new_investigation_id() -> str:
    return f"CASE-{secrets.token_hex(4).upper()}"


def new_invoice_id() -> str:
    return f"DOC-{secrets.token_hex(4).upper()}"


def intake_document(
    session: Session, filename: str, content: bytes, submitted_by: str, extractor: Extractor | None = None
) -> IntakeResult:
    # 1. Extract BEFORE touching the database, so no transaction is held open during a model call.
    try:
        result = extract_document(filename, content, extractor)
    except DocumentError as exc:
        raise IntakeRejected(422, str(exc)) from exc
    except LLMUnavailable as exc:
        raise IntakeRejected(
            503, "The AI extraction service is unavailable, so this document can't be read right now. "
            "Try again shortly, or upload the invoice as JSON (read without AI)."
        ) from exc  # fmt: skip
    if not result.ok:
        raise IntakeRejected(422, " ".join(result.warnings) + " Upload a clearer copy of the invoice.")
    return _store(session, result, filename, submitted_by)


def _store(session: Session, result: ExtractionResult, filename: str, submitted_by: str) -> IntakeResult:
    invoice = result.invoice
    assert invoice is not None

    # 2. Match the supplier by name; an unknown supplier is registered as UNVERIFIED (onboarding).
    match = find_best_supplier(
        invoice.supplier_name, repo.list_suppliers(session), get_settings().supplier_match_threshold
    )
    if match.supplier:
        supplier_id, match_kind = match.supplier.id, "MATCHED_EXISTING"
    else:
        supplier_id, match_kind = repo.next_supplier_id(session), "NEW_SUPPLIER"
        session.add(
            SupplierRow(
                id=supplier_id,
                name=invoice.supplier_name,
                contact_email=invoice.supplier_email,
                bank_account=invoice.bank_account,  # already masked
                bank_name=invoice.bank_name,
                risk_status="HIGH",
                verified=False,
            )
        )
        session.flush()
    # Invoice numbers are unique per supplier, not globally (ADR-045).
    existing = session.scalar(
        select(InvoiceRow.id).where(
            InvoiceRow.supplier_id == supplier_id, InvoiceRow.invoice_number == invoice.invoice_number
        )
    )
    if existing is not None:
        repo.append_audit(session, None, submitted_by, "Upload rejected: invoice number already on file",
                          f"{filename}: invoice {invoice.invoice_number} from {supplier_id} "
                          "was already uploaded.")  # fmt: skip
        raise IntakeRejected(409, f"Invoice {invoice.invoice_number} from {supplier_id} has already been uploaded.")
    invoice = invoice.model_copy(update={"id": new_invoice_id(), "supplier_id": supplier_id})

    # 3. Store the invoice and open a PENDING case.
    session.add(
        InvoiceRow(
            **repo.invoice_row_values(invoice),
            source_filename=filename,
            raw_text=result.redacted_text,  # full account number masked (POPIA)
            extraction_warnings=result.warnings,
            submitted_by=submitted_by,
        )
    )
    # Flush before the case row: without ORM relationships, SQLAlchemy doesn't order
    # these inserts by foreign key, and the case must reference an existing invoice.
    session.flush()
    investigation_id = new_investigation_id()
    session.add(InvestigationRow(id=investigation_id, invoice_id=invoice.id, supplier_id=supplier_id, status="PENDING"))
    session.flush()

    # 4. Audit trail.
    repo.append_audit(session, investigation_id, submitted_by, "Invoice uploaded",
                      f"{filename} ({result.source.kind}, {result.source.page_count} page(s)); "
                      f"fields read by {'code (JSON)' if result.method == 'json' else 'AI extraction'}.")  # fmt: skip
    if match_kind == "NEW_SUPPLIER":
        repo.append_audit(session, investigation_id, "system", "New supplier registered as unverified",
                          f"No existing supplier matched '{invoice.supplier_name}' (best similarity "
                          f"{match.confidence:.2f}). Registered as {supplier_id}, unverified.")  # fmt: skip
    else:
        repo.append_audit(session, investigation_id, "system", "Supplier matched",
                          f"'{invoice.supplier_name}' matched {supplier_id} ({match.supplier.name}), "
                          f"similarity {match.confidence:.2f}.")  # fmt: skip
    if result.warnings:
        repo.append_audit(session, investigation_id, "system", "Extraction warnings", " | ".join(result.warnings))

    return IntakeResult(
        investigation_id=investigation_id,
        invoice=invoice,
        supplier_id=supplier_id,
        supplier_match=match_kind,
        match_confidence=match.confidence,
        method=result.method,
        warnings=result.warnings,
    )
