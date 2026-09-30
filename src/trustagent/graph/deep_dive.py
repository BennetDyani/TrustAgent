"""The one place with agent autonomy: a bounded ReAct loop (ADR-001, ADR-031).

- Tools are READ-ONLY and scoped to the invoice under investigation. The agent
  can't query arbitrary suppliers, and there is no payment tool at all.
  Each tool runs in a ``READ ONLY`` database transaction, so even a bug
  can't write.
- The loop is built explicitly (model node -> ToolNode -> model ...) because
  ``langgraph.prebuilt.create_react_agent`` is deprecated in LangGraph 1.x.
- ``recursion_limit`` bounds it. If the limit is hit, the investigation
  continues without the deep dive and says so.
- The agent finishes by calling ``submit_findings``. Its observations go
  through the same whitelist as the AI review.
"""

import json
from collections.abc import Callable
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langchain_core.tools import StructuredTool
from langgraph.errors import GraphRecursionError
from langgraph.graph import END, START, MessagesState, StateGraph
from langgraph.prebuilt import ToolNode
from pydantic import BaseModel, Field
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session, sessionmaker

from trustagent.config import get_settings
from trustagent.db.models import InvestigationRow, InvoiceRow, SupplierRow, TransactionRow
from trustagent.graph.llm_steps import AIObservation
from trustagent.graph.prompts import DEEP_DIVE_SYSTEM
from trustagent.llm.factory import LLMUnavailable, get_chat_model
from trustagent.llm.usage import model_name, usage_of
from trustagent.rules.checks import last4

SUBMIT = "submit_findings"


@dataclass
class DeepDiveInput:
    investigation_id: str
    invoice_id: str
    supplier_id: str | None
    reason: str


@dataclass
class DeepDiveResult:
    summary: str
    observations: list[dict[str, Any]]
    completed: bool
    tool_calls: list[str] = field(default_factory=list)
    usage: list[dict[str, Any]] = field(default_factory=list)


DeepDiver = Callable[[DeepDiveInput], DeepDiveResult]


class SubmitFindings(BaseModel):
    """Finish the deep dive. Call exactly once, after using the other tools."""

    summary: str = Field(description="What you checked and what you concluded, in 2-4 sentences.")
    observations: list[AIObservation] = Field(description="New concerns only; empty if the evidence clears it.")


def _read_only(session_factory: sessionmaker) -> Session:
    session = session_factory()
    session.execute(text("SET TRANSACTION READ ONLY"))
    return session


def _money(value: Decimal | None) -> float | None:
    return float(value) if value is not None else None


def build_tools(session_factory: sessionmaker, inp: DeepDiveInput) -> list[StructuredTool]:
    """Read-only tools bound to this investigation (no ids are accepted from the model)."""

    def supplier_payment_history() -> str:
        """The supplier's completed payments: count, average, range, recent payments and bank accounts used."""
        if not inp.supplier_id:
            return json.dumps({"note": "No supplier record."})
        with _read_only(session_factory) as s:
            rows = s.scalars(
                select(TransactionRow)
                .where(TransactionRow.supplier_id == inp.supplier_id)
                .order_by(TransactionRow.date)
            ).all()
            done = [r for r in rows if r.status == "COMPLETED"]
            amounts = [r.amount for r in done]
            return json.dumps({
                "completed_payments": len(done),
                "average": _money(sum(amounts) / len(amounts)) if amounts else None,
                "min": _money(min(amounts)) if amounts else None,
                "max": _money(max(amounts)) if amounts else None,
                "bank_accounts_used": sorted({r.bank_account for r in rows}),
                "recent": [{"date": str(r.date), "amount": _money(r.amount), "description": r.description}
                           for r in done[-5:]],
            })  # fmt: skip

    def similar_invoices() -> str:
        """Other invoices that resemble this one: same supplier, same amount (any supplier), same bank account."""
        with _read_only(session_factory) as s:
            inv = s.get(InvoiceRow, inp.invoice_id)
            others = s.scalars(select(InvoiceRow).where(InvoiceRow.id != inp.invoice_id)).all()

            def brief(r: InvoiceRow) -> dict:
                return {"invoice": r.invoice_number, "supplier": r.supplier_name, "amount": _money(r.amount),
                        "date": str(r.date), "status": r.status}  # fmt: skip

            tolerance = inv.amount * Decimal("0.01")
            return json.dumps({
                "same_supplier": [brief(r) for r in others if r.supplier_id == inv.supplier_id],
                "same_amount_other_suppliers": [
                    brief(r) for r in others
                    if r.supplier_id != inv.supplier_id and abs(r.amount - inv.amount) <= tolerance
                ],
                "same_bank_account_other_suppliers": [
                    brief(r) for r in others
                    if r.supplier_id != inv.supplier_id and last4(r.bank_account) == last4(inv.bank_account)
                ],
            })  # fmt: skip

    def open_cases_for_supplier() -> str:
        """Other investigations for this supplier and their status and risk."""
        if not inp.supplier_id:
            return json.dumps({"note": "No supplier record."})
        with _read_only(session_factory) as s:
            cases = s.scalars(
                select(InvestigationRow).where(
                    InvestigationRow.supplier_id == inp.supplier_id, InvestigationRow.id != inp.investigation_id
                )
            ).all()
            supplier = s.get(SupplierRow, inp.supplier_id)
            total = s.scalar(
                select(func.count()).select_from(InvoiceRow).where(InvoiceRow.supplier_id == inp.supplier_id)
            )
            return json.dumps({
                "supplier": {"name": supplier.name, "verified": supplier.verified, "invoices_on_file": total},
                "other_cases": [{"case": c.id, "invoice": s.get(InvoiceRow, c.invoice_id).invoice_number,
                                 "status": c.status, "risk_level": c.risk_level,
                                 "decision": c.decision} for c in cases],
            })  # fmt: skip

    return [
        StructuredTool.from_function(supplier_payment_history),
        StructuredTool.from_function(similar_invoices),
        StructuredTool.from_function(open_cases_for_supplier),
    ]


def build_deep_dive_graph(llm: BaseChatModel, tools: list[StructuredTool]):
    submit_tool = StructuredTool.from_function(
        lambda **_: "submitted", name=SUBMIT, description=SubmitFindings.__doc__, args_schema=SubmitFindings
    )
    bound = llm.bind_tools([*tools, submit_tool])

    def agent(state: MessagesState) -> dict:
        return {"messages": [bound.invoke(state["messages"])]}

    def route(state: MessagesState) -> str:
        last = state["messages"][-1]
        calls = getattr(last, "tool_calls", None) or []
        if not calls or any(c["name"] == SUBMIT for c in calls):
            return END
        return "tools"

    graph = StateGraph(MessagesState)
    graph.add_node("agent", agent)
    graph.add_node("tools", ToolNode(tools))
    graph.add_edge(START, "agent")
    graph.add_conditional_edges("agent", route, {"tools": "tools", END: END})
    graph.add_edge("tools", "agent")
    return graph.compile()


def _result_from_messages(messages: list, completed: bool, llm_name: str) -> DeepDiveResult:
    ai_messages = [m for m in messages if isinstance(m, AIMessage)]
    usage = [usage_of(m, "deep_dive", llm_name) for m in ai_messages]
    tool_calls = [c["name"] for m in ai_messages for c in (m.tool_calls or []) if c["name"] != SUBMIT]
    for m in reversed(ai_messages):
        for call in m.tool_calls or []:
            if call["name"] == SUBMIT:
                args = call.get("args") or {}
                return DeepDiveResult(
                    summary=str(args.get("summary") or ""),
                    observations=[o for o in args.get("observations") or [] if isinstance(o, dict)],
                    completed=completed,
                    tool_calls=tool_calls,
                    usage=usage,
                )
    last_text = next((str(m.content) for m in reversed(ai_messages) if m.content), "")
    return DeepDiveResult(
        summary=last_text or "The deep dive ended without submitting findings.",
        observations=[],
        completed=False,
        tool_calls=tool_calls,
        usage=usage,
    )


def llm_deep_diver(session_factory: sessionmaker, llm: BaseChatModel | None = None) -> DeepDiver:
    def dive(inp: DeepDiveInput) -> DeepDiveResult:
        model = llm or get_chat_model("generator")
        graph = build_deep_dive_graph(model, build_tools(session_factory, inp))
        messages = [
            SystemMessage(DEEP_DIVE_SYSTEM.format(reason=inp.reason)),
            HumanMessage(f"Investigate the invoice under review. Use the tools, then call {SUBMIT}."),
        ]
        limit = get_settings().deep_dive_recursion_limit
        try:
            final = graph.invoke({"messages": messages}, {"recursion_limit": limit})
            return _result_from_messages(final["messages"], completed=True, llm_name=model_name(model))
        except GraphRecursionError:
            return DeepDiveResult(
                summary=f"The deep dive hit its step limit ({limit}) before finishing; no findings were added.",
                observations=[],
                completed=False,
            )
        except Exception as exc:  # provider errors: the investigation continues without the deep dive
            raise LLMUnavailable(f"deep_dive: {exc.__class__.__name__}: {exc}") from exc

    return dive
