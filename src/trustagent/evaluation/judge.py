"""LLM-as-judge for report faithfulness, and its validation against human labels.

- **A different provider from the generator** (config: JUDGE_PROVIDER=groq), so the judge doesn't share
  the generator's blind spots.
- **Rubric, reasoning first, then a PASS/FAIL verdict.** The schema puts ``reasoning`` before ``verdict``,
  so the model writes its justification before committing to an answer.
- **Validated against a human.** ``judge_validation/`` holds reports Bennet labels by hand; agreement
  (accuracy and Cohen's kappa) says how far the judge can be trusted.
"""

import csv
import json
from pathlib import Path
from typing import Any, Literal

from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import BaseModel, Field

from trustagent.config import PROJECT_ROOT
from trustagent.llm.factory import get_chat_model
from trustagent.llm.usage import invoke_structured

VALIDATION_DIR = PROJECT_ROOT / "evals" / "judge_validation"

RUBRIC = """You check whether an invoice-investigation report is FAITHFUL to its evidence.

You get exactly what the report writer was given: the INVOICE facts, the EVIDENCE, the REQUIRED NEXT STEPS
decided by code, the risk score and recommended action, and the AI DOCUMENT REVIEW and DEEP DIVE summaries.
Then the REPORT (summary and recommendation) written by another model.

A report PASSES only if ALL of these hold:
1. Every factual claim (amounts, names, bank details, dates, policy IDs, contract numbers, findings) is supported
   by what the writer was given. Paraphrase is fine; new facts are not. A claim resting only on the AI review or
   deep dive must not be presented as stronger than they state (e.g. "confirmed valid" when they say "no concerns").
2. It does not state or imply a finding that is not in the evidence (e.g. a bank change when there is none).
3. Its recommendation is consistent with the recommended action and the required next steps, and adds no
   steps that contradict them.
4. It does not contradict the evidence (e.g. calling a failed check a pass).
Minor omissions are acceptable: the report does not need to mention every piece of evidence.

Write your reasoning first, list each unsupported or contradicted claim, then give the verdict."""


class JudgeVerdict(BaseModel):
    reasoning: str = Field(description="Check each claim in the report against the evidence, step by step.")
    unsupported_claims: list[str] = Field(description="Claims not supported by, or contradicting, the evidence.")
    verdict: Literal["PASS", "FAIL"]


def judge_input(report: dict[str, Any]) -> str:
    evidence = "\n".join(
        f"- {e['type']} [{e['source']}, +{e['weight']}]: {e['description']}" for e in report.get("evidence", [])
    )
    steps = "\n".join(f"- {s}" for s in report.get("next_steps", []))
    # The same invoice facts the report writer was given; without them every correct invoice number or
    # amount in a report looks invented (the first judged run failed 33 of 46 reports for that reason).
    invoice = "\n".join(f"- {k}: {v}" for k, v in (report.get("invoice") or {}).items() if v not in (None, "", []))
    return f"""INVOICE:
{invoice or "- not provided"}

EVIDENCE:
{evidence or "- none"}

REQUIRED NEXT STEPS (code):
{steps or "- none"}

RISK: {report.get("score")}/100 ({report.get("level")}); RECOMMENDED ACTION: {report.get("recommended_action")}

AI DOCUMENT REVIEW (given to the writer): {report.get("ai_review_summary") or "unavailable"}
DEEP DIVE (given to the writer): {report.get("deep_dive_summary") or "not run"}

REPORT
Summary: {report.get("summary")}
Recommendation: {report.get("recommendation")}"""


def judge_report(report: dict[str, Any], llm=None) -> tuple[JudgeVerdict, dict[str, Any]]:
    messages = [SystemMessage(RUBRIC), HumanMessage(judge_input(report))]
    return invoke_structured(llm or get_chat_model("judge"), JudgeVerdict, messages, "judge")


# --- validation against a human ---------------------------------------------------------------------


def write_labelling_sheet(reports: dict[str, dict[str, Any]], n: int = 12) -> Path:
    """Write reports for Bennet to label by hand: a readable sheet plus a CSV to fill in."""
    VALIDATION_DIR.mkdir(parents=True, exist_ok=True)
    chosen = dict(list(reports.items())[:n])
    (VALIDATION_DIR / "reports.json").write_text(json.dumps(chosen, indent=2, default=str), encoding="utf-8")
    lines = ["# Judge validation: label each report PASS or FAIL",
             "", "Use the rubric below. Put your verdict in `human_labels.csv` (PASS or FAIL) and a short note.",
             "", "```", RUBRIC, "```", ""]  # fmt: skip
    for case_id, report in chosen.items():
        lines += [f"## {case_id}", "", "```", judge_input(report), "```", ""]
    (VALIDATION_DIR / "to_label.md").write_text("\n".join(lines), encoding="utf-8")
    labels = VALIDATION_DIR / "human_labels.csv"
    if not read_human_labels():  # never overwrite labels a person has started; otherwise match this sheet
        with labels.open("w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(["case_id", "verdict", "notes"])
            writer.writerows([[case_id, "", ""] for case_id in chosen])
    return VALIDATION_DIR / "to_label.md"


def read_human_labels() -> dict[str, str]:
    path = VALIDATION_DIR / "human_labels.csv"
    if not path.exists():
        return {}
    with path.open(encoding="utf-8") as f:
        return {r["case_id"]: r["verdict"].strip().upper() for r in csv.DictReader(f) if r["verdict"].strip()}
