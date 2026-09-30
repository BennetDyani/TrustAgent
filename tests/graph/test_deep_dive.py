"""The bounded ReAct loop, with a scripted tool-calling model (unit test fake)."""

from collections.abc import Iterator

import pytest
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, ToolMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from trustagent.domain import RiskLevel
from trustagent.graph.deep_dive import SUBMIT, DeepDiveInput, _read_only, build_tools, llm_deep_diver
from trustagent.graph.routing import deep_dive_reason


class ScriptedToolModel(BaseChatModel):
    """Returns the scripted AIMessages in order (repeating the last). Records what it was shown."""

    script: list[AIMessage]
    seen: list = []

    @property
    def _llm_type(self) -> str:
        return "scripted"

    def bind_tools(self, tools, **kwargs):
        return self

    def _generate(self, messages, stop=None, run_manager=None, **kwargs) -> ChatResult:
        self.seen.append(messages)
        msg = self.script[min(len(self.seen) - 1, len(self.script) - 1)]
        # A fresh copy each turn: add_messages de-duplicates by message id, so reusing one
        # object would replace the earlier message instead of appending a new turn.
        return ChatResult(generations=[ChatGeneration(message=msg.model_copy(update={"id": None}))])


def call(name: str, args: dict | None = None, i: int = 0) -> dict:
    return {"name": name, "args": args or {}, "id": f"call-{name}-{i}", "type": "tool_call"}


@pytest.fixture
def case(service, upload) -> Iterator[DeepDiveInput]:
    case_id = upload()
    list(service.start(case_id, __import__("trustagent.workflow.identity", fromlist=["x"]).DEMO_USERS["thandi"]))
    yield DeepDiveInput(case_id, "INV-1", "SUP-002", "Risk is MEDIUM.")


def test_agent_uses_tools_then_submits_findings(clean_db, case):
    model = ScriptedToolModel(script=[
        AIMessage("", tool_calls=[call("supplier_payment_history"), call("similar_invoices", i=1)]),
        AIMessage("", tool_calls=[call(SUBMIT, {
            "summary": "History is consistent; no duplicates.",
            "observations": [{"type": "OTHER", "description": "Minor note", "severity": "LOW", "quote": None}],
        })]),
    ], seen=[])  # fmt: skip
    result = llm_deep_diver(clean_db, model)(case)

    assert result.completed and result.summary == "History is consistent; no duplicates."
    assert result.tool_calls == ["supplier_payment_history", "similar_invoices"]
    assert result.observations[0]["type"] == "OTHER"
    tool_outputs = [m for m in model.seen[1] if isinstance(m, ToolMessage)]
    assert len(tool_outputs) == 2 and '"completed_payments": 1' in tool_outputs[0].content


def test_recursion_limit_stops_a_runaway_agent(clean_db, case):
    model = ScriptedToolModel(script=[AIMessage("", tool_calls=[call("similar_invoices")])], seen=[])
    result = llm_deep_diver(clean_db, model)(case)
    assert not result.completed and "step limit" in result.summary and result.observations == []


def test_agent_that_stops_without_submitting_is_marked_incomplete(clean_db, case):
    model = ScriptedToolModel(script=[AIMessage("I think it's fine.")], seen=[])
    result = llm_deep_diver(clean_db, model)(case)
    assert not result.completed and result.summary == "I think it's fine."


def test_tools_take_no_ids_from_the_model(clean_db, case):
    # Scoped to this investigation: the model can't point a tool at another supplier.
    for tool in build_tools(clean_db, case):
        assert tool.args == {}, tool.name


def test_tools_run_in_read_only_transactions(clean_db):
    with _read_only(clean_db) as s, pytest.raises(DBAPIError, match="read-only"):
        s.execute(text("UPDATE suppliers SET verified = true"))


def test_same_bank_account_across_suppliers_is_found(clean_db, upload, case):
    import json

    upload(invoice_number="INV-X", supplier_name="Other Traders", supplier_email="a@other.co.za",
           bank_account_holder="Other Traders", bank_account_number="0412039821 7733")  # fmt: skip
    tools = {t.name: t for t in build_tools(clean_db, case)}
    found = json.loads(tools["similar_invoices"].invoke({}))
    assert [r["invoice"] for r in found["same_bank_account_other_suppliers"]] == ["INV-X"]


# --- routing (ADR-015) --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "score, level, rules, ai, verified, history, expect",
    [
        (45, RiskLevel.MEDIUM, 2, 0, True, 5, "MEDIUM"),
        (27, RiskLevel.LOW, 1, 0, True, 5, "boundary"),
        (62, RiskLevel.HIGH, 3, 0, True, 5, "boundary"),
        (10, RiskLevel.LOW, 0, 1, True, 5, "AI review flagged"),
        (15, RiskLevel.LOW, 1, 0, False, 0, "no payment history"),
        (0, RiskLevel.LOW, 0, 0, True, 5, None),
        (95, RiskLevel.CRITICAL, 5, 1, True, 5, None),  # clearly critical: nothing to gain
    ],
)
def test_deep_dive_routing(score, level, rules, ai, verified, history, expect):
    reason = deep_dive_reason(score, level, rules, ai, verified, history)
    assert (reason is None) if expect is None else (expect in reason)
