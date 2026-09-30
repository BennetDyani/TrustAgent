"""The investigation service: the only way to start, resume, re-run or recover a run.

Callers (API, UI, notebooks) never drive the graph directly. That's where the
state guards live:

- **Start is atomic.** ``UPDATE ... SET status='IN_PROGRESS' WHERE status='PENDING'
  RETURNING run_number``. Two clicks can't start two runs (bug #12).
- **Decisions are serialised per case** with a Postgres advisory lock, so two
  approvers acting at once can't both resume the same checkpoint.
- **Crashes aren't dead ends.** A run left IN_PROGRESS by a crash is resumed
  from its last checkpoint, or marked FAILED so it can be re-run.
"""

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any

from langgraph.checkpoint.postgres import PostgresSaver
from langgraph.graph.state import CompiledStateGraph
from langgraph.types import Command
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool
from sqlalchemy import func, select, update
from sqlalchemy.orm import sessionmaker

from trustagent.config import get_settings
from trustagent.db import repository as repo
from trustagent.db.models import InvestigationRow
from trustagent.db.session import get_sessionmaker
from trustagent.domain import Approver, HumanAction, InvestigationStatus
from trustagent.graph.builder import build_graph, thread_id
from trustagent.graph.deep_dive import llm_deep_diver
from trustagent.graph.llm_steps import llm_reporter, llm_reviewer
from trustagent.graph.nodes import Deps
from trustagent.rag.context import rag_context
from trustagent.rag.embeddings import GeminiEmbedder
from trustagent.workflow.actions import ActionResult
from trustagent.workflow.approvals import ROLE_LABELS
from trustagent.workflow.guards import GuardError, check_can_rerun, check_can_start

Event = dict[str, Any]


class NotFound(LookupError):
    pass


def run_finished(state) -> bool:
    """True only when nothing is left to run.

    ``state.next`` alone is not enough: a step that completed just before a
    crash leaves its output as *pending writes*, and ``next`` then comes back
    empty although the run is unfinished. Found by the recovery test (ADR-032).
    """
    return not state.next and not state.tasks


def default_deps(session_factory: sessionmaker) -> Deps:
    return Deps(
        session_factory=session_factory,
        reviewer=llm_reviewer(),
        reporter=llm_reporter(),
        deep_diver=llm_deep_diver(session_factory),
        context_retriever=rag_context(GeminiEmbedder()),
    )


@dataclass
class InvestigationService:
    deps: Deps
    checkpointer: PostgresSaver
    graph: CompiledStateGraph
    _pool: ConnectionPool | None = None

    @classmethod
    def create(cls, database_url: str | None = None, deps: Deps | None = None) -> "InvestigationService":
        url = database_url or get_settings().database_url
        pool = ConnectionPool(
            url.replace("postgresql+psycopg://", "postgresql://", 1),
            kwargs={"autocommit": True, "prepare_threshold": 0, "row_factory": dict_row},
            open=True,
        )
        saver = PostgresSaver(pool)
        saver.setup()  # creates/migrates LangGraph's own checkpoint tables (idempotent)
        deps = deps or default_deps(get_sessionmaker(url))
        return cls(deps=deps, checkpointer=saver, graph=build_graph(deps, saver), _pool=pool)

    def close(self) -> None:
        if self._pool is not None:
            self._pool.close()

    def __enter__(self) -> "InvestigationService":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    # --- helpers ------------------------------------------------------------------------

    @property
    def _sf(self) -> sessionmaker:
        return self.deps.session_factory

    def _config(self, investigation_id: str, run_number: int) -> dict:
        return {"configurable": {"thread_id": thread_id(investigation_id, run_number)}}

    def _case(self, investigation_id: str) -> InvestigationRow:
        with self._sf() as s:
            case = s.get(InvestigationRow, investigation_id)
            if case is None:
                raise NotFound(f"Investigation {investigation_id} not found.")
            s.expunge(case)
            return case

    @contextmanager
    def _case_lock(self, investigation_id: str):
        """Serialise every graph resume for one case (transaction-scoped advisory lock)."""
        with self._sf() as s, s.begin():
            s.execute(select(func.pg_advisory_xact_lock(func.hashtext(investigation_id))))
            yield

    def _stream(self, graph_input: Any, config: dict, *, fail_case: str | None) -> Iterator[Event]:
        """Run the graph, translating its stream into API events. ``fail_case`` marks a crash as FAILED."""
        try:
            # durability="sync": each step's checkpoint is saved before the next step starts. The default
            # ("async") saves in the background, and a crash could lose it: found by the recovery test (ADR-032).
            for mode, chunk in self.graph.stream(
                graph_input, config, stream_mode=["custom", "updates"], durability="sync"
            ):
                if mode == "custom":
                    yield chunk
                elif "__interrupt__" in chunk:
                    yield {"event": "awaiting_decision", **chunk["__interrupt__"][0].value}
            state = self.graph.get_state(config)
            if run_finished(state):
                yield {"event": "complete", "closed": bool(state.values.get("closed"))}
        except Exception as exc:
            message = f"{exc.__class__.__name__}: {exc}"
            if fail_case:
                with self._sf() as s, s.begin():
                    s.execute(update(InvestigationRow).where(InvestigationRow.id == fail_case)
                              .values(status=InvestigationStatus.FAILED.value))  # fmt: skip
                    repo.append_audit(s, fail_case, "system", "Investigation failed", message[:2000], status="FAILED")
            yield {"event": "error", "message": message}

    # --- operations ---------------------------------------------------------------------

    def start(self, investigation_id: str, actor: Approver) -> Iterator[Event]:
        """Start the first run. Atomic: only one caller can move a case out of PENDING."""
        with self._sf() as s, s.begin():
            run = s.scalar(
                update(InvestigationRow)
                .where(InvestigationRow.id == investigation_id, InvestigationRow.status == "PENDING")
                .values(status="IN_PROGRESS", run_number=InvestigationRow.run_number + 1)
                .returning(InvestigationRow.run_number)
            )
            if run is None:
                check_can_start(InvestigationStatus(self._case(investigation_id).status))  # raises the clear error
            repo.append_audit(
                s, investigation_id, f"{actor.name} ({ROLE_LABELS[actor.role]})", "Investigation requested"
            )
        yield from self._stream(
            {"investigation_id": investigation_id, "run_number": run},
            self._config(investigation_id, run),
            fail_case=investigation_id,
        )

    def act(self, investigation_id: str, action: HumanAction, actor: Approver, note: str | None = None) -> ActionResult:
        """Resume the paused run with a human decision. ``actor`` comes from server-side identity only."""
        with self._case_lock(investigation_id):
            case = self._case(investigation_id)
            if case.status != InvestigationStatus.ACTION_REQUIRED:
                raise GuardError(f"Actions can only be taken on ACTION_REQUIRED cases; this one is {case.status}.")
            config = self._config(investigation_id, case.run_number)
            if self.graph.get_state(config).next != ("human_decision",):
                raise GuardError("This investigation is not waiting for a decision.")
            payload = {"action": HumanAction(action).value, "actor": actor.model_dump(mode="json"), "note": note}
            events = list(self._stream(Command(resume=payload), config, fail_case=None))
        for e in events:
            if e.get("event") == "action_result":
                return ActionResult(e["ok"], e["message"], e["closed"], e["status_code"])
        error = next((e["message"] for e in events if e.get("event") == "error"), "No result from the workflow.")
        return ActionResult(False, error, status_code=500)

    def rerun(self, investigation_id: str, actor: Approver) -> Iterator[Event]:
        """New run on a fresh thread: findings cleared, audit log kept (e.g. after supplier verification)."""
        with self._case_lock(investigation_id):
            with self._sf() as s, s.begin():
                case = s.get(InvestigationRow, investigation_id, with_for_update=True)
                if case is None:
                    raise NotFound(f"Investigation {investigation_id} not found.")
                check_can_rerun(InvestigationStatus(case.status))
                old_run = case.run_number
                case.status, case.run_number = "IN_PROGRESS", old_run + 1
                case.risk_score = case.risk_level = case.summary = case.recommendation = None
                case.recommended_action = case.decision = case.deep_dive_summary = None
                case.approvals, case.llm_usage = [], []  # new evidence, so earlier approvals no longer apply
                repo.append_audit(s, case.id, f"{actor.name} ({ROLE_LABELS[actor.role]})", "Investigation re-run",
                                  f"Run {old_run} superseded by run {old_run + 1}. "
                                  "Findings cleared; audit log kept.")  # fmt: skip
                run = case.run_number
            # The old run's checkpoints can never be resumed again.
            self.checkpointer.delete_thread(thread_id(investigation_id, old_run))
        yield from self._stream(
            {"investigation_id": investigation_id, "run_number": run},
            self._config(investigation_id, run),
            fail_case=investigation_id,
        )

    def recover(self, investigation_id: str) -> Iterator[Event]:
        """Continue a run left IN_PROGRESS by a crash, from its last checkpoint; or mark it FAILED."""
        case = self._case(investigation_id)
        if case.status != InvestigationStatus.IN_PROGRESS:
            raise GuardError(f"Only IN_PROGRESS runs can be recovered; this one is {case.status}.")
        config = self._config(investigation_id, case.run_number)
        state = self.graph.get_state(config)
        if run_finished(state):
            with self._sf() as s, s.begin():
                s.execute(update(InvestigationRow).where(InvestigationRow.id == investigation_id)
                          .values(status=InvestigationStatus.FAILED.value))  # fmt: skip
                repo.append_audit(s, investigation_id, "system", "Run could not be recovered",
                                  "No checkpoint to resume from; marked FAILED so it can be re-run.",
                                  status="FAILED")  # fmt: skip
            yield {"event": "error", "message": "No checkpoint to resume from; marked FAILED."}
            return
        with self._sf() as s, s.begin():
            repo.append_audit(
                s,
                investigation_id,
                "system",
                "Run recovered from checkpoint",
                f"Resuming after {', '.join(t.name for t in state.tasks) or 'the last step'}.",
            )
        yield from self._stream(None, config, fail_case=investigation_id)

    def checkpoint_history(self, investigation_id: str) -> list:
        case = self._case(investigation_id)
        return list(self.graph.get_state_history(self._config(investigation_id, case.run_number)))
