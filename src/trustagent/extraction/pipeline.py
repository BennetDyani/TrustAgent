"""File -> text -> structured fields -> validated, masked Invoice.

Each stage's output is kept on ``ExtractionResult``, so a notebook or the
audit log can show exactly what the model saw and returned.
"""

import re
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import PurePath
from typing import Literal

from trustagent.domain import Invoice
from trustagent.extraction.accounts import (
    amount_in_document,
    find_account_numbers,
    number_in_document,
    phrase_in_document,
)
from trustagent.extraction.documents import SourceDocument, load_document
from trustagent.extraction.json_invoice import parse_json_invoice
from trustagent.extraction.llm_extract import extract_with_llm
from trustagent.extraction.normalize import redact_account_numbers, to_invoice
from trustagent.extraction.schema import ExtractedInvoice
from trustagent.rules.checks import mask_account

Extractor = Callable[[str], ExtractedInvoice]


@dataclass
class ExtractionResult:
    source: SourceDocument
    method: Literal["json", "llm"]
    extracted: ExtractedInvoice
    invoice: Invoice | None
    # Document text with the full account number masked; safe to store and to send to later prompts.
    redacted_text: str
    warnings: list[str] = field(default_factory=list)
    missing_critical: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.invoice is not None


def extract_document(filename: str, content: bytes, extractor: Extractor | None = None) -> ExtractionResult:
    """Run the whole extraction. ``extractor`` defaults to the configured LLM.

    Raises ``DocumentError`` / ``OCRRequired`` for unreadable files and
    ``LLMUnavailable`` if the model is needed and fails. There's deliberately
    no regex fallback for free-form documents: guessed fields would feed the
    rules as if they were facts (ADR-024).
    """
    source = load_document(filename, content)
    if source.kind == "json":
        method, extracted = "json", parse_json_invoice(source.text)
    else:
        method, extracted = "llm", (extractor or extract_with_llm)(source.text)

    normalized = to_invoice(extracted, fallback_id=PurePath(filename).stem[:50])
    warnings = [*source.warnings, *(f"Extractor: {w}" for w in extracted.warnings), *normalized.warnings]
    invoice, missing = normalized.invoice, list(normalized.missing_critical)

    if invoice is not None and method == "llm":
        # Grounding (ADR-047): the model's values must literally appear in the document.
        if not number_in_document(extracted.bank_account_number, source.text):
            missing.append("bank account number (not found in the document)")
        if not amount_in_document(invoice.amount, source.text):
            missing.append("total amount (not found in the document)")
        if not phrase_in_document(invoice.supplier_name, source.text):
            warnings.append(f"The extracted supplier name '{invoice.supplier_name}' was not found in the document.")
        if missing:
            warnings.append(f"Could not verify against the document: {', '.join(missing)}.")
            invoice = None

    if invoice is not None:
        extracted_digits = re.sub(r"\D", "", extracted.bank_account_number or "")
        found = find_account_numbers(source.text) if method == "llm" else []
        if extracted_digits and not any(d.endswith(extracted_digits[-4:]) for d in found):
            found.insert(0, extracted_digits)
        invoice = invoice.model_copy(update={"document_accounts": [mask_account(d) for d in found]})

    return ExtractionResult(
        source=source,
        method=method,
        extracted=extracted,
        invoice=invoice,
        redacted_text=redact_account_numbers(source.text, extracted.bank_account_number),
        warnings=warnings,
        missing_critical=missing,
    )
