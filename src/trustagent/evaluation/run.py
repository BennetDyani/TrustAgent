"""One command for the whole evaluation (and a regression test after any prompt or model change).

Examples:
    uv run python -m trustagent.evaluation.run --offline                  # seconds, no API calls
    uv run python -m trustagent.evaluation.run --configs gemini --judge --retrieval
    uv run python -m trustagent.evaluation.run --configs gemini groq --judge --retrieval --repeats 3
    uv run python -m trustagent.evaluation.run --configs gemini --judge --from-run evals/runs/<ts>  # no re-run

Writes evals/runs/<timestamp>/results.json and report.md, and prints the headline table.
"""

import argparse
import datetime as dt
import json
from dataclasses import asdict
from pathlib import Path
from typing import Any

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from trustagent.config import PROJECT_ROOT, get_settings
from trustagent.evaluation import metrics as m
from trustagent.evaluation.harness import OFFLINE, RunConfig, load_cases, run
from trustagent.evaluation.judge import judge_report, read_human_labels, write_labelling_sheet
from trustagent.evaluation.thresholds import sweep
from trustagent.llm.factory import LLMUnavailable, get_chat_model


def configs(names: list[str]) -> list[RunConfig]:
    s = get_settings()
    known = {
        "offline": OFFLINE,
        "gemini": RunConfig("gemini", "gemini", s.llm_model if s.llm_provider == "gemini" else "gemini-3.5-flash-lite"),
        "groq": RunConfig("groq", "groq", "openai/gpt-oss-120b"),
        "gemini-flash": RunConfig("gemini-flash", "gemini", "gemini-3.5-flash"),
    }
    return [known[n] for n in names]


def load_saved(path: Path, cases: list[dict]) -> list[m.CaseResult]:
    """Results a previous run appended per case (``<config>.jsonl``), limited to ``cases``."""
    wanted = {c["id"] for c in cases}
    saved = [m.CaseResult(**json.loads(line)) for line in path.read_text(encoding="utf-8").splitlines() if line]
    return [r for r in saved if r.id in wanted]


def label_sample(results: list[m.CaseResult], n: int = 12) -> dict[str, dict]:
    """Reports for human labelling: fraud and non-fraud alternated, so the judge is checked on both."""
    with_report = [r for r in results if r.report.get("summary")]
    fraud, clean = [r for r in with_report if r.is_fraud], [r for r in with_report if not r.is_fraud]
    mixed = [r for pair in zip(fraud, clean, strict=False) for r in pair]
    rest = [r for r in with_report if r not in mixed]
    return {r.id: r.report for r in (mixed + rest)[:n]}


def summarise(results: list[m.CaseResult]) -> dict[str, Any]:
    return {
        "decisions": m.decision_metrics(results),
        "extraction": m.extraction_metrics(results),
        "rules": m.rule_metrics(results),
        "signals": m.signal_metrics(results),
        "cost": m.cost_metrics(results, get_settings().llm_prices_usd),
    }


def judge_all(
    config: RunConfig,
    results: list[m.CaseResult],
    gemini_judge_model: str | None = None,
    groq_judge_model: str | None = None,
) -> dict[str, Any]:
    # Never let a provider grade its own output.
    judge_provider = "gemini" if config.provider == "groq" else "groq"
    if judge_provider == "gemini":
        judge_model = gemini_judge_model or get_settings().llm_model
    else:
        judge_model = groq_judge_model or get_settings().judge_model
    llm = get_chat_model("judge", provider=judge_provider, model=judge_model)
    verdicts: dict[str, dict] = {}
    for r in results:
        if not r.report.get("summary"):
            continue
        try:
            v, _ = judge_report(r.report, llm)
            verdicts[r.id] = v.model_dump()
        except LLMUnavailable as exc:
            verdicts[r.id] = {"verdict": "ERROR", "reasoning": str(exc), "unsupported_claims": []}
    judged = [v for v in verdicts.values() if v["verdict"] in ("PASS", "FAIL")]
    human = read_human_labels()
    both = [cid for cid in human if cid in verdicts and verdicts[cid]["verdict"] in ("PASS", "FAIL")]
    return {
        "judge": f"{judge_provider}/{judge_model}",
        "faithfulness_pass_rate": round(sum(v["verdict"] == "PASS" for v in judged) / len(judged), 3)
        if judged
        else None,
        "failures": {cid: v["unsupported_claims"] for cid, v in verdicts.items() if v["verdict"] == "FAIL"},
        "human_agreement": {
            "labelled": len(both),
            "accuracy": round(sum(human[c] == verdicts[c]["verdict"] for c in both) / len(both), 3) if both else None,
            "cohens_kappa": m.cohens_kappa([human[c] for c in both], [verdicts[c]["verdict"] for c in both]),
        },
        "verdicts": verdicts,
    }


def headline(name: str, s: dict[str, Any], judge: dict | None) -> str:
    d, e, c = s["decisions"], s["extraction"], s["cost"]
    return (
        f"| {name} | {d['fraud_recall']} | {d['fraud_held_or_escalated']} | {d['false_positive_rate']} | "
        f"{d['decision_accuracy']} | {e['field_accuracy']} | {s['rules']['exact_match']} | "
        f"{s['signals']['contract_deviation']['recall']} | {judge['faithfulness_pass_rate'] if judge else '-'} | "
        f"{c['median_seconds']} | {c['mean_tokens']} | {c['cost_per_1000_invoices_usd']} |"
    )


HEADER = (
    "| Config | Fraud recall | Fraud held/escalated | False-positive rate | Decision accuracy | Extraction "
    "fields | Rules exact | Contract recall | Faithfulness | Median s | Tokens | USD / 1000 |\n"
    "|---|---|---|---|---|---|---|---|---|---|---|---|"
)


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--configs", nargs="+", default=["gemini"], choices=["offline", "gemini", "gemini-flash", "groq"])
    p.add_argument("--offline", action="store_true", help="shortcut for --configs offline")
    p.add_argument("--cases", nargs="*", help="case ids to run (default: all)")
    p.add_argument("--judge", action="store_true", help="LLM-as-judge faithfulness (different provider)")
    p.add_argument("--retrieval", action="store_true", help="Recall@K / MRR per search mode")
    p.add_argument("--repeats", type=int, default=1, help="repeat the first config N times on --repeat-cases")
    p.add_argument("--repeat-cases", nargs="*", default=[])
    p.add_argument("--label-sheet", action="store_true", help="write the judge-validation sheet from this run")
    p.add_argument("--gemini-judge-model", help="Gemini model that judges Groq-generated reports")
    p.add_argument("--groq-judge-model", help="Groq model that judges Gemini-generated reports")
    p.add_argument("--judge-configs", nargs="*", help="judge only these configs (default: all live ones)")
    p.add_argument("--from-run", type=Path, help="reuse <config>.jsonl results saved in this run folder")
    args = p.parse_args()

    names = ["offline"] if args.offline else args.configs
    cases = [c for c in load_cases() if not args.cases or c["id"] in args.cases]
    out_dir = PROJECT_ROOT / "evals" / "runs" / dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    out_dir.mkdir(parents=True, exist_ok=True)
    report: dict[str, Any] = {"cases": len(cases), "configs": {}, "started": dt.datetime.now().isoformat()}
    lines = [f"# Evaluation run {out_dir.name}", "", f"{len(cases)} cases.", "", HEADER]

    labelled = False  # the label sheet comes from the first live config only
    for config in configs(names):
        saved = args.from_run / f"{config.name}.jsonl" if args.from_run else None
        if saved and saved.exists():
            results = load_saved(saved, cases)
            (out_dir / saved.name).write_text(saved.read_text(encoding="utf-8"), encoding="utf-8")
        else:
            results = run(config, cases, save_to=out_dir / f"{config.name}.jsonl")
        summary = summarise(results)
        if args.label_sheet and not config.offline and not labelled:
            labelled = True  # before judging, so the judge can be compared with any labels already given
            write_labelling_sheet(label_sample(results))
        wants_judge = (
            args.judge and not config.offline and (not args.judge_configs or config.name in args.judge_configs)
        )
        judge = judge_all(config, results, args.gemini_judge_model, args.groq_judge_model) if wants_judge else None
        report["configs"][config.name] = {
            "model": config.model,
            "summary": summary,
            "judge": judge,
            "results": [asdict(r) for r in results],
        }
        lines.append(headline(f"{config.name} ({config.model or 'rules only'})", summary, judge))

    if args.repeats > 1 and args.repeat_cases:
        config = configs(names)[0]
        subset = [c for c in load_cases() if c["id"] in args.repeat_cases]
        repeats = {c["id"]: [] for c in subset}
        for _ in range(args.repeats):
            for r in run(config, subset):
                repeats[r.id].append(r)
        report["stability"] = m.stability(repeats)

    if args.retrieval:
        from trustagent.evaluation.retrieval import evaluate
        from trustagent.rag.embeddings import GeminiEmbedder

        with sessionmaker(bind=create_engine(get_settings().eval_database_url))() as session:
            report["retrieval"] = evaluate(session, GeminiEmbedder())
    report["holder_threshold"] = sweep(load_cases())

    (out_dir / "results.json").write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
    for name, cfg in report["configs"].items():
        d = cfg["summary"]["decisions"]
        lines += [
            "",
            f"## {name}",
            "",
            f"- Missed fraud: {d['missed_fraud'] or 'none'}",
            f"- False positives: {d['false_positives'] or 'none'}",
            f"- Wrong decisions: {d['wrong_decisions'] or 'none'}",
            f"- Rule mismatches: {cfg['summary']['rules']['mismatches'] or 'none'}",
            f"- Extraction mistakes: {cfg['summary']['extraction']['mistakes'] or 'none'}",
        ]
        if cfg["judge"]:
            lines += [
                f"- Faithfulness failures: {list(cfg['judge']['failures']) or 'none'}",
                f"- Judge vs human: {cfg['judge']['human_agreement']}",
            ]
    if "retrieval" in report:
        lines += ["", "## Retrieval", "", "| Mode | Recall@1 | Recall@3 | Recall@5 | MRR |", "|---|---|---|---|---|"]
        lines += [
            f"| {k} | {v['recall@1']} | {v['recall@3']} | {v['recall@5']} | {v['mrr']} |"
            for k, v in report["retrieval"]["modes"].items()
        ]
    if "stability" in report:
        st = report["stability"]
        lines += [
            "",
            "## Run-to-run stability",
            "",
            f"{st['cases']} cases x {st['repeats']} runs: level stable "
            f"{st['level_stable']}, action stable {st['action_stable']}",
            *[f"- {v}" for v in st["varied"]],
        ]
    (out_dir / "report.md").write_text("\n".join(lines), encoding="utf-8")
    print("\n".join(lines[: 6 + len(report["configs"])]))
    print(f"\nFull report: {out_dir / 'report.md'}")


if __name__ == "__main__":
    main()
