"""Text normalisation shared by the quote check and the extraction grounding checks."""

import re

_TYPOGRAPHY = str.maketrans({"…": "...", "’": "'", "‘": "'", "“": '"', "”": '"',
                             "–": "-", "—": "-", "*": None})  # fmt: skip


def normalise_text(text: str) -> str:
    """Casefold, collapse whitespace, drop markdown bold, straighten typographic quotes and dashes."""
    return re.sub(r"\s+", " ", text.translate(_TYPOGRAPHY)).strip().casefold()


def digits_pattern(number: str) -> str | None:
    """Regex matching these digits even when printed with spaces, dashes or markdown bold between them."""
    digits = re.sub(r"\D", "", number or "")
    if len(digits) < 4:
        return None
    return r"[\s*\-]*".join(re.escape(d) for d in digits)
