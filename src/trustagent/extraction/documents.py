"""Turn an uploaded file into text, honestly.

A scanned PDF has no text layer. Rather than guess, or call an OCR service
this project doesn't have, we say "OCR needed" (bug #10).
"""

import io
from dataclasses import dataclass, field
from pathlib import PurePath
from typing import Literal

from pypdf import PdfReader
from pypdf.errors import PdfReadError

MAX_UPLOAD_BYTES = 10 * 1024 * 1024
MAX_TEXT_CHARS = 20_000  # bounds the prompt; invoices are ~1-3k characters
MIN_CHARS_PER_PAGE = 30  # below this, a page is effectively an image

Kind = Literal["pdf", "markdown", "text", "json"]
_KINDS: dict[str, Kind] = {".pdf": "pdf", ".md": "markdown", ".markdown": "markdown", ".txt": "text", ".json": "json"}


class DocumentError(ValueError):
    """The file can't be read as an invoice (HTTP 422)."""


class OCRRequired(DocumentError):
    """The PDF has no usable text layer."""


@dataclass
class SourceDocument:
    filename: str
    kind: Kind
    text: str
    page_count: int = 1
    warnings: list[str] = field(default_factory=list)


def load_document(filename: str, content: bytes) -> SourceDocument:
    suffix = PurePath(filename).suffix.lower()
    kind = _KINDS.get(suffix)
    if kind is None:
        raise DocumentError(f"Unsupported file type '{suffix or filename}'. Use PDF, Markdown, text or JSON.")
    if not content:
        raise DocumentError("The file is empty.")
    if len(content) > MAX_UPLOAD_BYTES:
        raise DocumentError(f"The file is larger than {MAX_UPLOAD_BYTES // (1024 * 1024)} MB.")

    warnings: list[str] = []
    if kind == "pdf":
        text, pages = _pdf_text(content)
    else:
        pages = 1
        try:
            text = content.decode("utf-8-sig")
        except UnicodeDecodeError:
            text = content.decode("cp1252", errors="replace")
            warnings.append("File was not valid UTF-8; decoded as Windows-1252.")

    if len(text) > MAX_TEXT_CHARS and kind != "json":
        warnings.append(f"Document text was truncated to {MAX_TEXT_CHARS:,} characters.")
        text = text[:MAX_TEXT_CHARS]
    return SourceDocument(filename=filename, kind=kind, text=text, page_count=pages, warnings=warnings)


def _pdf_text(content: bytes) -> tuple[str, int]:
    try:
        reader = PdfReader(io.BytesIO(content))
        if reader.is_encrypted and not reader.decrypt(""):
            raise DocumentError("The PDF is password-protected.")
        pages = [page.extract_text() or "" for page in reader.pages]
    except PdfReadError as exc:
        raise DocumentError(f"Could not read the PDF: {exc}") from exc

    text = "\n".join(pages).strip()
    if len(text) < MIN_CHARS_PER_PAGE * max(len(pages), 1):
        raise OCRRequired(
            "This PDF has no text layer (it is probably a scanned image). OCR is needed to read it, and this "
            "system does not include OCR. Upload the original digital PDF, or a Markdown/JSON version."
        )
    return text, len(pages)
