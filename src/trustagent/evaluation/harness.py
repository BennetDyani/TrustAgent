"""Run evaluation cases through the real system, one clean database state per case.

Each case: reset the eval database (documents kept) -> preconditions (earlier uploads, extra
history) -> intake -> full investigation graph -> collect evidence, action, report, tokens, time.

Configurations:
- ``live``: the real pipeline with a chosen provider/model for every model step (extraction, AI
  review, deep dive, report). Embeddings are always Gemini: one embedding model everywhere.
- ``offline``: the real pipeline with the *gold* extraction and every model step unavailable. No API
  calls at all, so rules, scoring, fallback and routing can be regression-tested in seconds.
"""

import datetime as dt
import json
import time
from dataclasses import asdict, dataclass
from decimal import Decimal
from pathlib import Path
from typing import Any

from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from trustagent.config import PROJECT_ROOT, Provider, get_settings
from trustagent.db.models import InvestigationRow, SupplierRow, TransactionRow
from trustagent.db.seed import seed
from trustagent.evaluation.metrics import CaseResult
from trustagent.extraction.labels import compare
from trustagent.extraction.llm_extract import extract_with_llm
from trustagent.extraction.schema import ExtractedInvoice
from trustagent.graph.deep_dive import llm_deep_diver
from trustagent.graph.llm_steps import llm_reporter, llm_reviewer
from trustagent.graph.nodes import Deps
from trustagent.llm.factory import LLMUnavailable, get_chat_model
from trustagent.rag.context import policy_table_context, rag_context
from trustagent.rag.embeddings import GeminiEmbedder
from trustagent.rules.recommendation import required_next_steps
from trustagent.workflow.identity import DEMO_USERS
from trustagent.workflow.intake import IntakeRejected, intake_document
from trustagent.workflow.service import InvestigationService

APP_TABLES = "audit_log, evidence, investigations, invoices, transactions, suppliers, policies"
CHECKPOINTS = ("checkpoints", "checkpoint_blobs", "checkpoint_writes")


@dataclass(frozen=True)
class RunConfig:
    name: str
    provider: Provider | None = None  # None = offline
    model: str | None = None

    @property
    def offline(self) -> bool:
        return self.provider is None


OFFLINE = RunConfig("offline")


def load_cases(path: Path | None = None) -> list[dict[str, Any]]:
    return json.loads((path or PROJECT_ROOT / "evals" / "dataset" / "cases.json").read_text(encoding="utf-8"))


def _unavailable(_: Any) -> Any:
    raise LLMUnavailable("offline evaluation: model steps disabled")


class Harness:
    def __init__(self, config: RunConfig, database_url: str | None = None):
        self.config = config
        self.url = database_url or get_settings().eval_database_url
        self.engine = create_engine(self.url, pool_pre_ping=True)
        self.sf = sessionmaker(bind=self.engine, expire_on_commit=False)
        if config.offline:
            deps = Deps(self.sf, _unavailable, _unavailable, _unavailable, policy_table_context)
            self.extractor = None  # gold extraction per case
        else:
            llm = get_chat_model("generator", provider=config.provider, model=config.model)
            deps = Deps(
                self.sf,
                llm_reviewer(llm),
                llm_reporter(llm),
                llm_deep_diver(self.sf, llm),
                rag_context(GeminiEmbedder()),
            )
            self.extractor = lambda text: extract_with_llm(text, llm)
        self.service = InvestigationService.create(self.url, deps=deps)

    def close(self) -> None:
        self.service.close()
        self.engine.dispose()

    # --- environment ---------------------------------------------------------------------------------

    def reset(self) -> None:
        with self.engine.begin() as conn:
            conn.execute(text(f"TRUNCATE {APP_TABLES} RESTART IDENTITY CASCADE"))
            for t in CHECKPOINTS:
                if conn.execute(text("SELECT to_regclass(:t)"), {"t": t}).scalar():
                    conn.execute(text(f"TRUNCATE {t}"))
        with self.sf() as s, s.begin():
            seed(s)

    def preconditions(self, case: dict) -> None:
        pre = case.get("preconditions") or {}
        for name in pre.get("uploads", []):
            with self.sf() as s, s.begin():
                intake_document(s, name, (get_settings().invoices_dir / name).read_bytes(), "eval setup")
        for i, (supplier_id, amount) in enumerate(pre.get("history", [])):
            with self.sf() as s, s.begin():
                s.add(
                    TransactionRow(
                        id=f"TXN-E{i:03d}",
                        supplier_id=supplier_id,
                        invoice_id=f"HIST-{i}",
                        amount=Decimal(amount),
                        bank_account="****2190",
                        date=dt.date(2026, 3 + i, 15),
                        status="COMPLETED",
                        description="eval history",
                    )
                )

    # --- one case --------------------------------------------------------------------------------------

    def run_case(self, case: dict) -> CaseResult:
        r = CaseResult(
            id=case["id"],
            scenario=case["scenario"],
            is_fraud=case["is_fraud"],
            acceptable_actions=case["acceptable_actions"],
            expected_rules=case["expected_rules"],
            expected_ai=case.get("expected_ai", []),
            expected_contract_deviation=case["expected_contract_deviation"],
            expect_intake_status=case.get("expect_intake_status"),
        )
        if self.config.offline and not case.get("gold_extraction") and case.get("expect_intake_status") is None:
            r.error = "skipped offline (no gold extraction)"
            return r
        self.reset()
        self.preconditions(case)
        path = PROJECT_ROOT / case["file"]
        extractor = self.extractor
        if self.config.offline:
            gold = case.get("gold_extraction")
            extractor = (lambda _t, g=gold: ExtractedInvoice.model_validate(g)) if gold else _unavailable

        started = time.perf_counter()
        try:
            with self.sf() as s, s.begin():
                intake = intake_document(s, path.name, path.read_bytes(), "eval", extractor=extractor)
        except IntakeRejected as exc:
            cause = f" (cause: {exc.__cause__})" if exc.__cause__ else ""
            r.intake_status, r.intake_error = exc.status_code, (exc.message + cause)[:500]
            r.seconds = round(time.perf_counter() - started, 1)
            return r
        if case.get("fields"):
            r.field_checks = {k: ok for k, (_e, _a, ok) in compare(intake.invoice, case["fields"]).items()}

        events = list(self.service.start(intake.investigation_id, DEMO_USERS["thandi"]))
        r.seconds = round(time.perf_counter() - started, 1)
        if err := next((e["message"] for e in events if e["event"] == "error"), None):
            r.error = err
        evidence = [e for e in events if e["event"] == "evidence"]
        flagged = [e for e in evidence if e["type"] != "CONFIRMED_MATCH"]
        r.rule_types = sorted({e["type"] for e in flagged if e["source"] == "RULE"})
        r.ai_types = sorted({e["type"] for e in flagged if e["source"] == "AI"})
        r.contract_deviation = any(e["type"] == "CONTRACT_DEVIATION" for e in flagged)
        r.deep_dive_ran = any(e.get("action") == "Deep dive started" for e in events)
        rec = next((e for e in events if e["event"] == "recommendation"), {})
        with self.sf() as s:
            case_row = s.get(InvestigationRow, intake.investigation_id)
            r.risk_score, r.risk_level, r.action = case_row.risk_score, case_row.risk_level, case_row.recommended_action
            r.ai_review_available, r.usage = case_row.ai_review_available, list(case_row.llm_usage or [])
            supplier = s.get(SupplierRow, case_row.supplier_id) if case_row.supplier_id else None
            verified = bool(supplier and supplier.verified)
            deep_dive_summary, run_number = case_row.deep_dive_summary, case_row.run_number
        state = self.service.graph.get_state(self.service._config(intake.investigation_id, run_number)).values
        inv = intake.invoice
        r.report = {
            # Everything the report writer was given, so the judge can check the report against the same facts.
            "invoice": {
                "invoice_number": inv.invoice_number,
                "supplier_name": inv.supplier_name,
                "amount": f"R{inv.amount:,.2f}" if inv.amount is not None else None,
                "invoice_date": inv.date,
                "due_date": inv.due_date,
            },  # fmt: skip
            "ai_review_summary": state.get("ai_summary") if r.ai_review_available else None,
            "deep_dive_summary": deep_dive_summary,
            "summary": rec.get("summary"),
            "recommendation": rec.get("recommendation"),
            "recommended_action": rec.get("recommended_action"),
            "score": r.risk_score,
            "level": r.risk_level,
            "evidence": [{k: e[k] for k in ("type", "source", "weight", "description", "citations")} for e in evidence],
            "next_steps": required_next_steps({e["type"] for e in flagged}, verified, intake.invoice.amount),
        }
        return r


def _transient_failure(r: CaseResult) -> bool:
    """A provider hiccup (quota burst, 503 overloaded), not a property of the system being evaluated."""
    return (r.intake_status == 503 and r.expect_intake_status is None) or r.ai_review_available is False


def run(
    config: RunConfig, cases: list[dict], progress: bool = True, retry_wait: float = 60.0, save_to: Path | None = None
) -> list[CaseResult]:
    """Run cases; a case hit by a transient provider failure is retried (up to 3 attempts, recorded).

    With ``save_to``, each result is appended as a JSON line as soon as it finishes, so a stopped run
    (e.g. a provider's daily quota running out) keeps everything it completed.
    """
    harness = Harness(config)
    results = []
    try:
        for i, case in enumerate(cases, 1):
            result = harness.run_case(case)
            attempts = 1
            while not config.offline and _transient_failure(result) and attempts < 3:
                time.sleep(retry_wait)
                result, attempts = harness.run_case(case), attempts + 1
            result.attempts = attempts
            if result.error and result.error.startswith("skipped offline"):
                continue  # originals have no gold extraction; they only run live
            results.append(result)
            if save_to is not None:
                with save_to.open("a", encoding="utf-8") as f:
                    f.write(json.dumps(asdict(result), default=str) + "\n")
            if progress:
                error = f"  ERROR {result.error}" if result.error else ""
                outcome = result.action or result.intake_status
                print(f"[{config.name}] {i:2d}/{len(cases)} {case['id']:9s} {result.risk_level or '-':8s} "
                      f"{outcome}  ({result.seconds}s){error}", flush=True)  # fmt: skip
    finally:
        harness.close()
    return results
