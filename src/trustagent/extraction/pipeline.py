"""File -> text -> structured fields -> validated, masked Invoice.

Each stage's output is kept on ``ExtractionResult``, so a notebook or the
audit log can show exactly what the model saw and returned.
"""

from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import PurePath
from typing import Literal

from trustagent.domain import Invoice
from trustagent.extraction.documents import SourceDocument, load_document
from trustagent.extraction.json_invoice import parse_json_invoice
from trustagent.extraction.llm_extract import extract_with_llm
from trustagent.extraction.normalize import redact_account_numbers, to_invoice
from trustagent.extraction.schema import ExtractedInvoice

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
    return ExtractionResult(
        source=source,
        method=method,
        extracted=extracted,
        invoice=normalized.invoice,
        redacted_text=redact_account_numbers(source.text, extracted.bank_account_number),
        warnings=warnings,
        missing_critical=normalized.missing_critical,
    )
