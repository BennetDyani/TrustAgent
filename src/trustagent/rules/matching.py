"""Supplier-name similarity, used for supplier matching and the account-holder check."""

import re
from collections.abc import Iterable
from dataclasses import dataclass

from trustagent.domain import Supplier

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


@dataclass(frozen=True)
class SupplierMatch:
    supplier: Supplier | None
    confidence: float


def find_best_supplier(name: str, suppliers: Iterable[Supplier], threshold: float) -> SupplierMatch:
    """Best name match at or above ``threshold``; otherwise no supplier.

    No match means a genuinely new supplier. It is registered as unverified
    rather than attached to the closest unrelated record.
    """
    best, best_score = None, 0.0
    for supplier in suppliers:
        score = name_similarity(name, supplier.name)
        if score > best_score:
            best, best_score = supplier, score
    return SupplierMatch(best if best_score >= threshold else None, best_score)
