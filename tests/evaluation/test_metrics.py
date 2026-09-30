"""The evaluation's own arithmetic, tested: a wrong metric would mislead every decision taken from it."""

import pytest

from trustagent.evaluation import metrics as m
from trustagent.evaluation.thresholds import sweep


def case(id_, fraud, ok, action, rules=(), expected=(), **kw) -> m.CaseResult:
    return m.CaseResult(id=id_, scenario="", is_fraud=fraud, acceptable_actions=list(ok), expected_rules=list(expected),
                        rule_types=list(rules), action=action, **kw)  # fmt: skip


def test_fraud_recall_counts_anything_but_approval_as_stopped():
    results = [
        case("a", True, ["HOLD_PAYMENT"], "HOLD_PAYMENT"),
        case("b", True, ["HOLD_PAYMENT"], "REQUEST_VERIFICATION"),  # stopped, though not the ideal action
        case("c", True, ["HOLD_PAYMENT"], "APPROVE_PAYMENT"),  # missed
        case("d", False, ["APPROVE_PAYMENT"], "APPROVE_PAYMENT"),
    ]
    d = m.decision_metrics(results)
    assert d["fraud_recall"] == pytest.approx(2 / 3, abs=1e-3)
    assert d["fraud_held_or_escalated"] == pytest.approx(1 / 3, abs=1e-3)
    assert d["missed_fraud"] == ["c"]
    assert d["decision_accuracy"] == 0.5  # a and d


def test_false_positive_rate_only_counts_payable_invoices():
    results = [
        case("clean-ok", False, ["APPROVE_PAYMENT"], "APPROVE_PAYMENT"),
        case("clean-held", False, ["APPROVE_PAYMENT"], "HOLD_PAYMENT"),
        case("new-supplier", False, ["REQUEST_VERIFICATION"], "REQUEST_VERIFICATION"),  # policy friction, not a FP
    ]
    d = m.decision_metrics(results)
    assert d["false_positive_rate"] == 0.5 and d["false_positives"] == ["clean-held"]


def test_rejected_upload_counts_as_stopped_and_meets_its_expectation():
    r = case("scan", False, [], None, expect_intake_status=422, intake_status=422)
    assert m.stopped(r)
    assert m.decision_metrics([r])["intake_expectations_met"] == 1.0


def test_rule_precision_and_recall():
    results = [
        case("a", True, [], "HOLD_PAYMENT", rules=["BANK_DETAILS_CHANGED"], expected=["BANK_DETAILS_CHANGED"]),
        case(
            "b",
            True,
            [],
            "HOLD_PAYMENT",
            rules=["BANK_DETAILS_CHANGED", "URGENCY_INDICATOR"],
            expected=["BANK_DETAILS_CHANGED"],
        ),  # fmt: skip
        case("c", True, [], "HOLD_PAYMENT", rules=[], expected=["DUPLICATE_INVOICE"]),
    ]
    r = m.rule_metrics(results)
    assert r["exact_match"] == pytest.approx(1 / 3, abs=1e-3)
    assert r["per_rule"]["BANK_DETAILS_CHANGED"] == {"precision": 1.0, "recall": 1.0, "tp": 2, "fp": 0, "fn": 0}
    assert r["per_rule"]["URGENCY_INDICATOR"]["precision"] == 0.0
    assert r["per_rule"]["DUPLICATE_INVOICE"]["recall"] == 0.0


def test_extraction_accuracy_per_field():
    results = [case("a", False, [], None, field_checks={"amount": True, "urgency": False}),
               case("b", False, [], None, field_checks={"amount": True, "urgency": True})]  # fmt: skip
    e = m.extraction_metrics(results)
    assert e["field_accuracy"] == 0.75 and e["per_field"] == {"amount": 1.0, "urgency": 0.5}
    assert e["mistakes"] == ["a: urgency"] and e["documents_fully_correct"] == 0.5


def test_cost_uses_per_model_prices():
    r = case("a", False, [], None, usage=[{"step": "report", "model": "m", "input_tokens": 1_000_000,
                                           "output_tokens": 500_000}])  # fmt: skip
    c = m.cost_metrics([r], {"m": (0.30, 2.50)})
    assert c["mean_cost_usd"] == pytest.approx(0.30 + 1.25)
    assert c["mean_tokens_by_step"] == {"report": 1_500_000}


def test_recall_at_k_and_mrr():
    ranked = [[False, True, False], [True, False, False], [False, False, False]]
    assert m.recall_at_k(ranked, 1) == pytest.approx(1 / 3, abs=1e-3)
    assert m.recall_at_k(ranked, 3) == pytest.approx(2 / 3, abs=1e-3)
    assert m.mean_reciprocal_rank(ranked) == pytest.approx((0.5 + 1 + 0) / 3, abs=1e-3)


def test_cohens_kappa():
    assert m.cohens_kappa(["PASS", "FAIL", "PASS"], ["PASS", "FAIL", "PASS"]) == 1.0
    assert m.cohens_kappa(["PASS", "PASS", "FAIL", "FAIL"], ["PASS", "FAIL", "PASS", "FAIL"]) == 0.0


def test_stability_detects_variation():
    runs = {"x": [case("x", True, [], "HOLD_PAYMENT", risk_level="HIGH"),
                  case("x", True, [], "REQUEST_VERIFICATION", risk_level="MEDIUM")],
            "y": [case("y", False, [], "APPROVE_PAYMENT", risk_level="LOW")] * 2}  # fmt: skip
    s = m.stability(runs)
    assert s["level_stable"] == 0.5 and s["action_stable"] == 0.5 and s["varied"][0].startswith("x:")


def test_holder_threshold_sweep_finds_the_boundary_case():
    cases = [
        {"fields": {"bank_account_holder": "Prestige Events Holdings", "supplier_name": "Prestige Catering & Events"}},
        {
            "fields": {
                "bank_account_holder": "Metro Cleaning Services CC",
                "supplier_name": "Metro Cleaning Services CC",
            }
        },
    ]
    rows = {r["threshold"]: r for r in sweep(cases)["sweep"]}
    assert rows[0.5]["missed"] == ["Prestige Events Holdings"]  # exactly 0.5 passes a "< 0.5" rule
    assert rows[0.51]["missed"] == [] and rows[0.51]["legit_flagged"] == 0


def test_label_sample_alternates_fraud_and_clean_and_skips_reportless_cases():
    from trustagent.evaluation.run import label_sample

    results = [case(f"f{i}", True, [], "HOLD_PAYMENT", report={"summary": "s"}) for i in range(3)]
    results += [case(f"c{i}", False, [], "APPROVE_PAYMENT", report={"summary": "s"}) for i in range(3)]
    results.append(case("scan", False, [], None))  # rejected at intake: no report to label
    assert list(label_sample(results, n=4)) == ["f0", "c0", "f1", "c1"]
    assert "scan" not in label_sample(results)
