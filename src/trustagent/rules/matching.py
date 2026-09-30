"""Supplier-name similarity, used for supplier matching and the account-holder check."""

import re

# Generic business-entity words that add noise to a name comparison.
_ENTITY_WORDS = re.compile(r"\b(pty|ltd|inc|llc|corp|co|solutions|services|group|company)\b")


def normalize_name(name: str) -> str:
    """Lowercase, strip punctuation and entity words, collapse whitespace."""
    cleaned = re.sub(r"[^\w\s]", " ", name.lower())
    cleaned = _ENTITY_WORDS.sub("", cleaned)
    return re.sub(r"\s+", " ", cleaned).strip()


def name_similarity(a: str, b: str) -> float:
    """1.0 for equal names, 0.85 when one contains the other, else token Jaccard."""
    na, nb = normalize_name(a), normalize_name(b)
    if not na or not nb:
        return 0.0
    if na == nb:
        return 1.0
    if na in nb or nb in na:
        return 0.85
    tokens_a, tokens_b = set(na.split(" ")), set(nb.split(" "))
    return len(tokens_a & tokens_b) / len(tokens_a | tokens_b)
