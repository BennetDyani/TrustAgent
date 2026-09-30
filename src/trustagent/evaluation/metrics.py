"""Evaluation metrics, as pure functions over case results (tested in tests/evaluation).

Why these metrics: a missed fraud costs the full invoice amount; a false alarm costs a phone call.
So **fraud recall** (payment stopped) comes first, then how often legitimate, payable invoices get
unnecessary friction (**false-positive rate**), then everything that explains them: extraction
accuracy, whether the right rules fired, whether contract deviations were found.
"""

from collections import Counter
from dataclasses import dataclass, field
from statistics import mean, median
from typing import Any

APPROVE = "APPROVE_PAYMENT"
STOPPING = {"HOLD_PAYMENT", "ESCALATE"}


@dataclass
class CaseResult:
    id: str
    scenario: str
    is_fraud: bool
    acceptable_actions: list[str]
    expected_rules: list[str]
    expected_ai: list[str] = field(default_factory=list)
    expected_contract_deviation: bool = False
    expect_intake_status: int | None = None
    # what happened
    intake_status: int = 201
    intake_error: str | None = None
    field_checks: dict[str, bool] = field(default_factory=dict)
    rule_types: list[str] = field(default_factory=list)
    ai_types: list[str] = field(default_factory=list)
    contract_deviation: bool = False
    risk_score: int | None = None
    risk_level: str | None = None
    action: str | None = None
    ai_review_available: bool | None = None
    deep_dive_ran: bool = False
    seconds: float = 0.0
    usage: list[dict[str, Any]] = field(default_factory=list)
    report: dict[str, Any] = field(default_factory=dict)  # summary, recommendation, evidence, next_steps
    error: str | None = None
    attempts: int = 1  # >1 when a transient provider failure forced a retry


def _rate(numerator: int, denominator: int) -> float | None:
    return round(numerator / denominator, 3) if denominator else None


def stopped(r: CaseResult) -> bool:
    """Payment did not go straight through: a recommendation other than approve, or the upload was refused."""
    return r.intake_status >= 400 or (r.action is not None and r.action != APPROVE)


def decision_metrics(results: list[CaseResult]) -> dict[str, Any]:
    ran = [r for r in results if r.expect_intake_status is None]
    fraud = [r for r in ran if r.is_fraud]
    payable = [r for r in ran if r.acceptable_actions == [APPROVE]]
    intake_expected = [r for r in results if r.expect_intake_status is not None]
    return {
        "cases": len(results),
        "decision_accuracy": _rate(sum(r.action in r.acceptable_actions for r in ran), len(ran)),
        "fraud_recall": _rate(sum(stopped(r) for r in fraud), len(fraud)),
        "fraud_held_or_escalated": _rate(sum(r.action in STOPPING for r in fraud), len(fraud)),
        "false_positive_rate": _rate(sum(stopped(r) for r in payable), len(payable)),
        "missed_fraud": [r.id for r in fraud if not stopped(r)],
        "false_positives": [r.id for r in payable if stopped(r)],
        "wrong_decisions": [
            f"{r.id}: {r.action} (acceptable {'/'.join(r.acceptable_actions)})"
            for r in ran
            if r.action not in r.acceptable_actions
        ],
        "intake_expectations_met": _rate(
            sum(r.intake_status == r.expect_intake_status for r in intake_expected), len(intake_expected)
        ),
        "errors": [f"{r.id}: {r.error}" for r in results if r.error],
        "retried_cases": [f"{r.id} x{r.attempts}" for r in results if r.attempts > 1],
        "provider_failures_after_retries": [
            f"{r.id}: {r.intake_error or 'AI review unavailable'}"
            for r in results
            if r.intake_status == 503 or r.ai_review_available is False
        ],
    }


def extraction_metrics(results: list[CaseResult]) -> dict[str, Any]:
    checked = [r for r in results if r.field_checks]
    per_field: dict[str, list[bool]] = {}
    for r in checked:
        for name, ok in r.field_checks.items():
            per_field.setdefault(name, []).append(ok)
    total = [ok for oks in per_field.values() for ok in oks]
    return {
        "documents": len(checked),
        "field_accuracy": _rate(sum(total), len(total)),
        "documents_fully_correct": _rate(sum(all(r.field_checks.values()) for r in checked), len(checked)),
        "per_field": {k: _rate(sum(v), len(v)) for k, v in sorted(per_field.items())},
        "mistakes": [f"{r.id}: {k}" for r in checked for k, ok in r.field_checks.items() if not ok],
    }


def rule_metrics(results: list[CaseResult]) -> dict[str, Any]:
    """Did exactly the expected rules fire? Per-rule precision and recall over all cases that ran."""
    ran = [r for r in results if r.intake_status < 400 and r.expect_intake_status is None]
    tp: Counter = Counter()
    fp: Counter = Counter()
    fn: Counter = Counter()
    for r in ran:
        got, want = set(r.rule_types), set(r.expected_rules)
        tp.update(got & want)
        fp.update(got - want)
        fn.update(want - got)
    types = sorted(set(tp) | set(fp) | set(fn))
    return {
        "exact_match": _rate(sum(set(r.rule_types) == set(r.expected_rules) for r in ran), len(ran)),
        "per_rule": {
            t: {
                "precision": _rate(tp[t], tp[t] + fp[t]),
                "recall": _rate(tp[t], tp[t] + fn[t]),
                "tp": tp[t],
                "fp": fp[t],
                "fn": fn[t],
            }
            for t in types
        },
        "mismatches": [
            f"{r.id}: got {sorted(set(r.rule_types) - set(r.expected_rules))}, "
            f"missing {sorted(set(r.expected_rules) - set(r.rule_types))}"
            for r in ran
            if set(r.rule_types) != set(r.expected_rules)
        ],
    }


def signal_metrics(results: list[CaseResult]) -> dict[str, Any]:
    ran = [r for r in results if r.intake_status < 400 and r.expect_intake_status is None]
    contract_tp = sum(r.contract_deviation and r.expected_contract_deviation for r in ran)
    contract_fp = sum(r.contract_deviation and not r.expected_contract_deviation for r in ran)
    contract_fn = sum(not r.contract_deviation and r.expected_contract_deviation for r in ran)
    ai_cases = [r for r in ran if r.expected_ai]
    return {
        "contract_deviation": {
            "precision": _rate(contract_tp, contract_tp + contract_fp),
            "recall": _rate(contract_tp, contract_tp + contract_fn),
        },
        "ai_signal_recall": _rate(sum(set(r.expected_ai) <= set(r.ai_types) for r in ai_cases), len(ai_cases)),
        "ai_review_available": _rate(sum(bool(r.ai_review_available) for r in ran), len(ran)),
        "deep_dive_rate": _rate(sum(r.deep_dive_ran for r in ran), len(ran)),
    }


def cost_metrics(results: list[CaseResult], prices: dict[str, tuple[float, float]]) -> dict[str, Any]:
    ran = [r for r in results if r.usage]
    per_case_cost, per_case_tokens = [], []
    by_step: dict[str, list[int]] = {}
    for r in ran:
        cost = tokens = 0
        for u in r.usage:
            p_in, p_out = prices.get(u["model"], (0.0, 0.0))
            cost += (u["input_tokens"] * p_in + u["output_tokens"] * p_out) / 1_000_000
            tokens += u["input_tokens"] + u["output_tokens"]
            by_step.setdefault(u["step"], []).append(u["input_tokens"] + u["output_tokens"])
        per_case_cost.append(cost)
        per_case_tokens.append(tokens)
    seconds = sorted(r.seconds for r in results if r.seconds)
    return {
        "mean_tokens": round(mean(per_case_tokens)) if per_case_tokens else None,
        "mean_cost_usd": round(mean(per_case_cost), 5) if per_case_cost else None,
        "cost_per_1000_invoices_usd": round(mean(per_case_cost) * 1000, 2) if per_case_cost else None,
        "mean_tokens_by_step": {k: round(mean(v)) for k, v in sorted(by_step.items())},
        "median_seconds": round(median(seconds), 1) if seconds else None,
        "p95_seconds": round(seconds[max(0, int(len(seconds) * 0.95) - 1)], 1) if seconds else None,
    }


def stability(repeats: dict[str, list[CaseResult]]) -> dict[str, Any]:
    """Run-to-run variation: how often the level / the action differ across repeats of the same case."""
    level_stable = action_stable = 0
    varied = []
    for case_id, runs in repeats.items():
        levels = {r.risk_level for r in runs}
        actions = {r.action for r in runs}
        level_stable += len(levels) == 1
        action_stable += len(actions) == 1
        if len(levels) > 1 or len(actions) > 1:
            varied.append(
                f"{case_id}: levels {sorted(map(str, levels))}, actions {sorted(map(str, actions))}, "
                f"scores {[r.risk_score for r in runs]}"
            )
    n = len(repeats)
    return {
        "cases": n,
        "repeats": len(next(iter(repeats.values()))) if repeats else 0,
        "level_stable": _rate(level_stable, n),
        "action_stable": _rate(action_stable, n),
        "varied": varied,
    }


def recall_at_k(ranked_relevance: list[list[bool]], k: int) -> float | None:
    """Share of questions with at least one relevant result in the top k."""
    return _rate(sum(any(r[:k]) for r in ranked_relevance), len(ranked_relevance))


def mean_reciprocal_rank(ranked_relevance: list[list[bool]]) -> float | None:
    if not ranked_relevance:
        return None
    return round(mean(next((1 / (i + 1) for i, rel in enumerate(r) if rel), 0.0) for r in ranked_relevance), 3)


def cohens_kappa(a: list[str], b: list[str]) -> float | None:
    """Agreement between two raters beyond chance (judge vs human)."""
    if not a or len(a) != len(b):
        return None
    labels = set(a) | set(b)
    observed = sum(x == y for x, y in zip(a, b, strict=True)) / len(a)
    expected = sum((a.count(lab) / len(a)) * (b.count(lab) / len(b)) for lab in labels)
    return round((observed - expected) / (1 - expected), 3) if expected < 1 else 1.0
