"""Deterministic checks on the document itself (weak spot 2, ADR-047).

The model extracts fields, but it must not be the only thing deciding which
bank account an invoice pays. Code checks two things:

1. **Grounding.** The extracted account number and total must literally
   appear in the document. A value the model produced from anywhere else
   (injected instructions, a "correction", a hallucination) blocks the invoice.
2. **Every account number in the document is found**, by a scanner that looks
   next to "account" / "acc" / "a/c" labels. A document that lists two
   different accounts is a rule finding (MULTIPLE_BANK_ACCOUNTS).
"""

import re
from decimal import Decimal

from trustagent.text import digits_pattern, normalise_text

_LABEL = re.compile(r"(?i)\b(?:account|acc|a/c)\b")
_EXCLUDE = re.compile(r"(?i)holder|name|@")
_CANDIDATE = re.compile(r"(?<![\w/])\d[\d \-*]{6,}\d(?![\w/])")
_VALUE_ONLY = re.compile(r"^[\s|]*\d[\d \-*]{6,}\d[\s|*]*$")
_MIN_ACCOUNT_DIGITS = 8
_MONEY = re.compile(r"-?\s?R?\s?\d[\d ,.]*\d")


def _digits(text: str) -> str:
    return re.sub(r"\D", "", text)


def find_account_numbers(text: str) -> list[str]:
    """Distinct bank account numbers (digits only) near an account label, in document order."""
    lines = text.splitlines()
    found: list[str] = []
    for i, line in enumerate(lines):
        if not _LABEL.search(line) or _EXCLUDE.search(line):
            continue
        after_label = line[_LABEL.search(line).end() :]
        candidates = [_digits(m.group(0)) for m in _CANDIDATE.finditer(after_label)]
        if not candidates:
            # PDF tables often put the value on the next line ("Account Number\n51007733829904").
            nxt = next((ln for ln in lines[i + 1 : i + 3] if ln.strip()), "")
            if _VALUE_ONLY.match(nxt):
                candidates = [_digits(nxt)]
        for digits in candidates:
            if len(digits) >= _MIN_ACCOUNT_DIGITS and digits not in found:
                found.append(digits)
    return found


def number_in_document(number: str | None, text: str) -> bool:
    pattern = digits_pattern(number or "")
    return bool(pattern and re.search(pattern, text))


def phrase_in_document(phrase: str | None, text: str) -> bool:
    return bool(phrase and phrase.strip()) and normalise_text(phrase) in normalise_text(text)


def amount_in_document(amount: Decimal, text: str) -> bool:
    """Is this amount printed anywhere in the document (in any common format)?"""
    from trustagent.extraction.normalize import parse_amount

    return any(parse_amount(m.group(0)) in (amount, -amount) for m in _MONEY.finditer(text))
