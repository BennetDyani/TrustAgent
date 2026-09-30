"""Threshold sweep for the account-holder check (ADR-021, deferred to phase 6).

Pure and free: name similarity over every (account holder, supplier name) pair in the dataset, with
a label saying whether the holder is legitimately the supplier. For each candidate threshold, count
legitimate holders that would be flagged (false positives) and illegitimate ones that would pass.
"""

from typing import Any

from trustagent.rules.matching import name_similarity

# Holders known NOT to be the supplier (personal names, holding companies, other entities).
ILLEGITIMATE_HOLDERS = {"T Ndlovu", "J Dube", "K Mokoena", "Q-Ship Trading", "Prestige Events Holdings"}


def holder_pairs(cases: list[dict[str, Any]]) -> list[tuple[str, str, bool]]:
    pairs = set()
    for c in cases:
        f = c.get("fields") or {}
        holder, supplier = f.get("bank_account_holder"), f.get("supplier_name")
        if holder and supplier:
            pairs.add((holder, supplier, holder not in ILLEGITIMATE_HOLDERS))
    return sorted(pairs)


def sweep(cases: list[dict[str, Any]], thresholds=(0.3, 0.4, 0.5, 0.51, 0.6, 0.7, 0.8)) -> dict[str, Any]:
    pairs = holder_pairs(cases)
    rows = []
    for t in thresholds:
        flagged_legit = [h for h, s, legit in pairs if legit and name_similarity(h, s) < t]
        missed_bad = [h for h, s, legit in pairs if not legit and name_similarity(h, s) >= t]
        rows.append({"threshold": t, "legit_flagged": len(flagged_legit), "illegitimate_missed": len(missed_bad),
                     "missed": missed_bad, "flagged": flagged_legit})  # fmt: skip
    return {
        "pairs": [
            {"holder": h, "supplier": s, "legitimate": legit, "similarity": round(name_similarity(h, s), 2)}
            for h, s, legit in pairs
        ],  # fmt: skip
        "sweep": rows,
    }
