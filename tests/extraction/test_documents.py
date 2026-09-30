import io

import pytest
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas

from trustagent.config import get_settings
from trustagent.extraction.documents import MAX_TEXT_CHARS, DocumentError, OCRRequired, load_document


def _pdf(text: str | None) -> bytes:
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=A4)
    if text:
        y = 800
        for line in text.splitlines():
            c.drawString(50, y, line)
            y -= 14
    else:
        c.rect(50, 50, 400, 600, fill=1)  # an "image" with no text layer, like a scan
    c.save()
    return buf.getvalue()


def test_sample_pdf_has_text():
    content = (get_settings().invoices_dir / "INV-2005-nexus-advisory-CRITICAL.pdf").read_bytes()
    doc = load_document("INV-2005.pdf", content)
    assert doc.kind == "pdf" and doc.page_count == 1
    assert "NX-0917" in doc.text and "51007733829904" in doc.text


def test_scanned_pdf_reports_ocr_needed():
    with pytest.raises(OCRRequired, match="OCR is needed"):
        load_document("scan.pdf", _pdf(None))


def test_generated_text_pdf_is_read():
    doc = load_document("x.pdf", _pdf("INVOICE INV-9\nTotal due R 1,000.00\nAccount Number 123456789"))
    assert "INV-9" in doc.text


def test_corrupt_pdf_is_a_document_error():
    with pytest.raises(DocumentError):
        load_document("bad.pdf", b"%PDF-1.4 this is not really a pdf")


@pytest.mark.parametrize("name", ["invoice.docx", "invoice.exe", "invoice"])
def test_unsupported_types_rejected(name):
    with pytest.raises(DocumentError, match="Unsupported"):
        load_document(name, b"data")


def test_empty_file_rejected():
    with pytest.raises(DocumentError, match="empty"):
        load_document("a.md", b"")


def test_markdown_utf8_with_bom():
    assert load_document("a.md", "﻿# Invoice — R 100".encode()).text == "# Invoice — R 100"


def test_non_utf8_falls_back_with_warning():
    doc = load_document("a.txt", "Café R 100".encode("cp1252"))
    assert doc.text == "Café R 100" and doc.warnings


def test_long_text_is_truncated_with_warning():
    doc = load_document("a.txt", b"x" * (MAX_TEXT_CHARS + 10))
    assert len(doc.text) == MAX_TEXT_CHARS and "truncated" in doc.warnings[0]
