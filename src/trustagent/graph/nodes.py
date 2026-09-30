"""Graph nodes. Each is small and does one thing.

Nodes that write to the database do so in their own transaction, and they
all come *before* the ``human_decision`` node or *after* it
(``execute_action``). The node that calls ``interrupt()`` has no side effects,
because LangGraph re-runs it from the top on every resume (we observed this:
a probe node ran 4 times for 2 pauses).
"""

from collections.abc import Callable
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from langgraph.config import get_stream_writer
from langgraph.types import interrupt
from sqlalchemy import delete, select
from sqlalchemy.orm import sessionmaker

from trustagent.db import repository as repo
from trustagent.db.models import EvidenceRow, InvestigationRow, InvoiceRow, PolicyRow
from trustagent.domain import Approver, HumanAction, RiskIndicator, RiskLevel
from trustagent.graph.deep_dive import DeepDiveInput, DeepDiver
from trustagent.graph.llm_steps import Reporter, ReportInput, Reviewer, ReviewInput
from trustagent.graph.routing import deep_dive_reason
from trustagent.graph.state import InvestigationState
from trustagent.llm.factory import LLMUnavailable
from trustagent.rules.checks import run_rule_checks
from trustagent.rules.recommendation import (
    fallback_recommendation,
    filter_ai_indicators,
    minimum_action,
    quote_supported,
    required_next_steps,
    resolve_recommended_action,
)
from trustagent.rules.scoring import calculate_risk
from trustagent.workflow.actions import apply_human_action

ContextRetriever = Callable[[Any, dict, dict | None], list[dict[str, Any]]]


def policy_context(session, invoice: dict, supplier: dict | None) -> list[dict[str, Any]]:
    """Phase 3 context: the policy table, with citations. Replaced by RAG retrieval in phase 4."""
    return [
        {"source_id": p.id, "title": p.name, "text": p.rule, "citation": {"source": p.id, "section": p.name}}
        for p in session.scalars(select(PolicyRow).order_by(PolicyRow.id))
    ]


@dataclass
class Deps:
    session_factory: sessionmaker
    reviewer: Reviewer
    reporter: Reporter
    deep_diver: DeepDiver
    context_retriever: ContextRetriever = policy_context


def _emit(event: str, **data: Any) -> None:
    """Custom stream event for the UI/API (``stream_mode="custom"``)."""
    get_stream_writer()({"event": event, **data})


def _activity(action: str, detail: str = "", status: str = "COMPLETED") -> None:
    _emit("activity", action=action, detail=detail, status=status)


def _flags(findings: list[dict]) -> list[dict]:
    return [f for f in findings if f["type"] != "CONFIRMED_MATCH"]


def _all_findings(state: InvestigationState) -> list[dict]:
    return [
        *state.get("rule_findings", []),
        *state.get("contract_findings", []),
        *state.get("ai_findings", []),
        *state.get("deep_dive_findings", []),
    ]


def make_nodes(deps: Deps) -> dict[str, Callable]:
    sf = deps.session_factory

    def load_context(state: InvestigationState) -> dict:
        with sf() as s, s.begin():
            case = s.get(InvestigationRow, state["investigation_id"])
            ctx = repo.load_check_context(s, case.invoice_id)
            row = s.get(InvoiceRow, case.invoice_id)
            repo.append_audit(s, case.id, "system", f"Investigation run {state['run_number']} started",
                              f"Invoice {case.invoice_id}, supplier {case.supplier_id}.")  # fmt: skip
            out = {
                "invoice": ctx.invoice.model_dump(mode="json"),
                "supplier": ctx.supplier.model_dump(mode="json") if ctx.supplier else None,
                "supplier_verified": bool(ctx.supplier and ctx.supplier.verified),
                "history_count": sum(1 for t in ctx.history if t.status == "COMPLETED"),
                "invoice_text": row.raw_text or "",
                "notes": [f"Extraction warning: {w}" for w in row.extraction_warnings or []],
            }
        _activity("Invoice and supplier loaded", f"{ctx.invoice.id} from {ctx.invoice.supplier_name}")
        return out

    def rule_checks(state: InvestigationState) -> dict:
        with sf() as s:
            ctx = repo.load_check_context(s, state["invoice"]["id"])
        findings = [f.model_dump(mode="json") for f in run_rule_checks(ctx)]
        flagged = _flags(findings)
        _activity("Rule checks completed",
                  f"{len(flagged)} finding(s), {len(findings) - len(flagged)} check(s) passed")  # fmt: skip
        return {"rule_findings": findings}

    def retrieve_context(state: InvestigationState) -> dict:
        with sf() as s:
            context = deps.context_retriever(s, state["invoice"], state.get("supplier"))
        _activity("Policies and contracts retrieved", ", ".join(c["source_id"] for c in context) or "none found")
        return {"context": context, "contract_findings": []}

    def ai_review(state: InvestigationState) -> dict:
        warnings = [
            n.removeprefix("Extraction warning: ") for n in state.get("notes", []) if n.startswith("Extraction")
        ]
        inp = ReviewInput(
            invoice_text=state.get("invoice_text", ""),
            rule_findings=state["rule_findings"],
            supplier=state.get("supplier"),
            history_count=state.get("history_count", 0),
            extraction_warnings=warnings,
            context=state.get("context", []),
        )
        try:
            review, usage = deps.reviewer(inp)
        except LLMUnavailable as exc:
            _activity("AI review unavailable", "Continuing with rule checks only.", status="FAILED")
            return {"ai_findings": [], "ai_review_available": False, "ai_summary": "",
                    "notes": [f"AI review unavailable: {exc}"]}  # fmt: skip
        findings, ignored = [], []
        for obs in review.observations:  # one at a time, so each keeps its own quote
            if not quote_supported(obs.quote, inp.invoice_text):
                ignored.append(f"{obs.type} (quote not found in the invoice)")  # ADR-041
                continue
            accepted, rejected = filter_ai_indicators([obs])
            ignored += rejected
            quote = f' (Invoice says: "{obs.quote}")' if obs.quote else ""
            findings += [{**a.model_dump(mode="json"), "description": a.description + quote} for a in accepted]
        _activity("AI document review completed", f"{len(findings)} observation(s)")
        return {"ai_findings": findings, "ai_review_available": True, "ai_summary": review.summary,
                "ignored_ai_types": ignored, "usage": [usage]}  # fmt: skip

    def provisional_score(state: InvestigationState) -> dict:
        risk = calculate_risk(RiskIndicator.model_validate(f) for f in _all_findings(state))
        reason = deep_dive_reason(
            risk.score, risk.level,
            rule_flag_count=len(_flags(state["rule_findings"])),
            ai_flag_count=len(state.get("ai_findings", [])),
            supplier_verified=state.get("supplier_verified", False),
            history_count=state.get("history_count", 0),
        )  # fmt: skip
        return {"provisional": {"score": risk.score, "level": risk.level.value, "deep_dive_reason": reason}}

    def route_after_provisional(state: InvestigationState) -> str:
        return "deep_dive" if state["provisional"]["deep_dive_reason"] else "score"

    def deep_dive(state: InvestigationState) -> dict:
        reason = state["provisional"]["deep_dive_reason"]
        _activity("Deep dive started", reason, status="IN_PROGRESS")
        inp = DeepDiveInput(state["investigation_id"], state["invoice"]["id"], state["invoice"].get("supplier_id"),
                            reason)  # fmt: skip
        try:
            result = deps.deep_diver(inp)
        except LLMUnavailable as exc:
            _activity("Deep dive unavailable", "Continuing without it.", status="FAILED")
            return {"deep_dive": {"ran": False, "reason": reason, "summary": None, "completed": False},
                    "deep_dive_findings": [], "notes": [f"Deep dive unavailable: {exc}"]}  # fmt: skip
        accepted, ignored = filter_ai_indicators(result.observations)
        findings = [{**a.model_dump(mode="json"), "description": f"Deep dive: {a.description}"} for a in accepted]
        _activity("Deep dive completed" if result.completed else "Deep dive incomplete",
                  f"Tools used: {', '.join(result.tool_calls) or 'none'}. {result.summary}")  # fmt: skip
        return {
            "deep_dive": {"ran": True, "reason": reason, "summary": result.summary, "completed": result.completed,
                          "tool_calls": result.tool_calls},
            "deep_dive_findings": findings, "ignored_ai_types": ignored, "usage": result.usage,
        }  # fmt: skip

    def score(state: InvestigationState) -> dict:
        risk = calculate_risk(RiskIndicator.model_validate(f) for f in _all_findings(state))
        indicators = [i.model_dump(mode="json") for i in risk.indicators]
        with sf() as s, s.begin():
            case = s.get(InvestigationRow, state["investigation_id"])
            # Evidence for this run is (re)written in one transaction; earlier runs' rows are kept.
            s.execute(delete(EvidenceRow).where(EvidenceRow.investigation_id == case.id,
                                                EvidenceRow.run_number == state["run_number"]))  # fmt: skip
            for i in indicators:
                s.add(EvidenceRow(investigation_id=case.id, run_number=state["run_number"], indicator_type=i["type"],
                                  description=i["description"], severity=i["severity"], source=i["source"],
                                  weight=i["weight"], citations=i.get("citations", [])))  # fmt: skip
            case.risk_score, case.risk_level = risk.score, risk.level.value
            case.ai_review_available = state.get("ai_review_available", False)
            case.deep_dive_summary = (state.get("deep_dive") or {}).get("summary")
            repo.append_audit(s, case.id, "system", f"Risk assessed: {risk.score}/100 ({risk.level.value})",
                              f"{len(_flags(indicators))} finding(s) counted; each type counts once.")  # fmt: skip
        # Evidence is always streamed BEFORE the score, so a score never appears without its support.
        for i in indicators:
            _emit("evidence", **i)
        _emit("risk", score=risk.score, level=risk.level.value)
        return {"risk": {"score": risk.score, "level": risk.level.value, "indicators": indicators}}

    def report(state: InvestigationState) -> dict:
        risk = state["risk"]
        level = RiskLevel(risk["level"])
        types = {i["type"] for i in risk["indicators"]}
        verified = state.get("supplier_verified", False)
        floor = minimum_action(level, verified, types)
        ai_ok = state.get("ai_review_available", False)
        usage: list[dict] = []
        notes: list[str] = []
        used_fallback, raised = False, False
        try:
            draft, u = deps.reporter(ReportInput(
                invoice=state["invoice"], score=risk["score"], level=level.value, minimum_action=floor.value,
                evidence=risk["indicators"], ai_review_available=ai_ok, ai_summary=state.get("ai_summary", ""),
                deep_dive_summary=(state.get("deep_dive") or {}).get("summary"),
                next_steps=required_next_steps(types, verified, Decimal(str(state["invoice"]["amount"]))),
            ))  # fmt: skip
            usage = [u]
            action, raised = resolve_recommended_action(draft.recommended_action, level, verified, types)
            summary, recommendation = draft.summary, draft.recommendation
            if raised:
                recommendation += (
                    f" [Action set to {action.value}: the proposed {draft.recommended_action} is below the minimum.]"
                )
            if not ai_ok:
                summary += " (The AI document review was unavailable; this assessment is based on rule checks only.)"
        except LLMUnavailable as exc:
            used_fallback = True
            notes = [f"Report writer unavailable: {exc}"]
            action, recommendation = fallback_recommendation(level, verified, ai_ok, types)
            flagged = [i["type"] for i in risk["indicators"] if i["type"] != "CONFIRMED_MATCH"]
            summary = (
                f"Automated summary: {len(flagged)} finding(s): {'; '.join(flagged)}."
                if flagged
                else "No risk indicators were found by the rule checks."
            )
            summary += " The AI report writer was unavailable, so this report was compiled from the rule findings."

        with sf() as s, s.begin():
            case = s.get(InvestigationRow, state["investigation_id"])
            case.summary, case.recommendation, case.recommended_action = summary, recommendation, action.value
            case.status = "ACTION_REQUIRED"
            case.llm_usage = [*state.get("usage", []), *usage]
            title = "Investigation report generated" + (" (rule-based fallback)" if used_fallback else "")
            detail = f"Recommended {action.value}" + (" (raised to the minimum)" if raised else "") + "."
            repo.append_audit(s, case.id, "agent", title, detail)
        _emit("recommendation", summary=summary, recommendation=recommendation, recommended_action=action.value)
        return {
            "report": {"summary": summary, "recommendation": recommendation, "recommended_action": action.value,
                       "raised": raised, "fallback": used_fallback},
            "usage": usage, "notes": notes, "closed": False,
        }  # fmt: skip

    def human_decision(state: InvestigationState) -> dict:
        # NO side effects here: this node re-runs from the top on every resume.
        payload = interrupt({
            "investigation_id": state["investigation_id"],
            "recommended_action": state["report"]["recommended_action"],
            "options": [a.value for a in HumanAction],
            "last_action_result": state.get("last_action_result"),
        })  # fmt: skip
        return {"pending_action": payload}

    def execute_action(state: InvestigationState) -> dict:
        p = state["pending_action"] or {}
        with sf() as s, s.begin():
            result = apply_human_action(
                s,
                state["investigation_id"],
                HumanAction(p["action"]),
                Approver.model_validate(p["actor"]),
                p.get("note"),
            )
        _emit("action_result", ok=result.ok, message=result.message, closed=result.closed,
              status_code=result.status_code)  # fmt: skip
        return {"last_action_result": {"ok": result.ok, "message": result.message, "status_code": result.status_code},
                "closed": result.closed, "pending_action": None}  # fmt: skip

    def route_after_action(state: InvestigationState) -> str:
        return "end" if state.get("closed") else "human_decision"

    return {
        "load_context": load_context,
        "rule_checks": rule_checks,
        "retrieve_context": retrieve_context,
        "ai_review": ai_review,
        "provisional_score": provisional_score,
        "route_after_provisional": route_after_provisional,
        "deep_dive": deep_dive,
        "score": score,
        "report": report,
        "human_decision": human_decision,
        "execute_action": execute_action,
        "route_after_action": route_after_action,
    }
