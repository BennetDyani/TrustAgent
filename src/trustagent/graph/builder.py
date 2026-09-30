"""Assemble the investigation graph.

load_context -> rule_checks -> retrieve_context -> ai_review -> provisional_score
    -> [MEDIUM or ambiguous] deep_dive -> score        (bounded ReAct, read-only tools)
    -> [otherwise]                     -> score
score -> report -> human_decision (interrupt) -> execute_action
    -> [case still open] human_decision   (dual approval, escalation, verification)
    -> [closed] END
"""

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph

from trustagent.graph.nodes import Deps, make_nodes
from trustagent.graph.state import InvestigationState


def build_graph(deps: Deps, checkpointer: BaseCheckpointSaver | None = None) -> CompiledStateGraph:
    n = make_nodes(deps)
    g = StateGraph(InvestigationState)
    for name in (
        "load_context", "rule_checks", "retrieve_context", "ai_review", "provisional_score",
        "deep_dive", "score", "report", "human_decision", "execute_action",
    ):  # fmt: skip
        g.add_node(name, n[name])

    g.add_edge(START, "load_context")
    g.add_edge("load_context", "rule_checks")
    g.add_edge("rule_checks", "retrieve_context")
    g.add_edge("retrieve_context", "ai_review")
    g.add_edge("ai_review", "provisional_score")
    # Explicit path maps, so the Mermaid diagram shows every branch.
    g.add_conditional_edges(
        "provisional_score", n["route_after_provisional"], {"deep_dive": "deep_dive", "score": "score"}
    )
    g.add_edge("deep_dive", "score")
    g.add_edge("score", "report")
    g.add_edge("report", "human_decision")
    g.add_edge("human_decision", "execute_action")
    g.add_conditional_edges("execute_action", n["route_after_action"], {"human_decision": "human_decision", "end": END})
    return g.compile(checkpointer=checkpointer)


def thread_id(investigation_id: str, run_number: int) -> str:
    """One LangGraph thread per run (ADR-006): a re-run starts clean, and the audit log spans runs."""
    return f"{investigation_id}:run-{run_number}"
