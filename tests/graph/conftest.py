"""Graph tests: real LangGraph + real Postgres checkpointer + committed test database.

Only the LLM steps are fakes (unit tests only). Each test starts from a
truncated, freshly seeded database.
"""

from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.orm import sessionmaker

from tests.extraction.test_normalize import extracted
from trustagent.config import get_settings
from trustagent.db.seed import seed
from trustagent.graph.deep_dive import DeepDiveInput, DeepDiveResult
from trustagent.graph.llm_steps import AIObservation, AIReview, ReportDraft, ReportInput, ReviewInput
from trustagent.graph.nodes import Deps
from trustagent.llm.factory import LLMUnavailable
from trustagent.workflow.intake import intake_document
from trustagent.workflow.service import InvestigationService

APP_TABLES = "audit_log, evidence, investigations, invoices, transactions, suppliers, policies, document_chunks"
CHECKPOINT_TABLES = ("checkpoints", "checkpoint_blobs", "checkpoint_writes")
USAGE = {"step": "fake", "model": "fake", "input_tokens": 10, "output_tokens": 5}


@dataclass
class FakeLLM:
    """Scriptable stand-ins for the three LLM steps; records what they were given."""

    observations: list[AIObservation] = field(default_factory=list)
    proposed_action: str | None = None  # None -> propose exactly the minimum
    review_down: bool = False
    report_down: bool = False
    dive_observations: list[dict] = field(default_factory=list)
    review_inputs: list[ReviewInput] = field(default_factory=list)
    report_inputs: list[ReportInput] = field(default_factory=list)
    dive_inputs: list[DeepDiveInput] = field(default_factory=list)

    def reviewer(self, inp: ReviewInput):
        self.review_inputs.append(inp)
        if self.review_down:
            raise LLMUnavailable("simulated outage")
        return AIReview(observations=self.observations, summary="Fake review."), USAGE

    def reporter(self, inp: ReportInput):
        self.report_inputs.append(inp)
        if self.report_down:
            raise LLMUnavailable("simulated outage")
        action = self.proposed_action or inp.minimum_action
        return ReportDraft(summary="Fake summary.", recommendation="Fake recommendation.",
                           recommended_action=action), USAGE  # fmt: skip

    def deep_diver(self, inp: DeepDiveInput) -> DeepDiveResult:
        self.dive_inputs.append(inp)
        return DeepDiveResult(summary="Fake deep dive.", observations=self.dive_observations, completed=True,
                              tool_calls=["similar_invoices"], usage=[USAGE])  # fmt: skip


def _truncate(engine) -> None:
    with engine.begin() as conn:
        conn.execute(text(f"TRUNCATE {APP_TABLES} RESTART IDENTITY CASCADE"))
        existing = {r[0] for r in conn.execute(text("SELECT tablename FROM pg_tables WHERE schemaname='public'"))}
        for t in CHECKPOINT_TABLES:
            if t in existing:
                conn.execute(text(f"TRUNCATE {t}"))


@pytest.fixture
def clean_db(db_engine) -> Iterator[sessionmaker]:
    """Committed data, truncated before AND after, so rollback-based tests elsewhere see an empty database."""
    _truncate(db_engine)
    sf = sessionmaker(bind=db_engine, expire_on_commit=False)
    with sf() as s, s.begin():
        seed(s)
    yield sf
    _truncate(db_engine)


@pytest.fixture
def fake_llm() -> FakeLLM:
    return FakeLLM()


@pytest.fixture
def service(clean_db, fake_llm) -> Iterator[InvestigationService]:
    deps = Deps(session_factory=clean_db, reviewer=fake_llm.reviewer, reporter=fake_llm.reporter,
                deep_diver=fake_llm.deep_diver)  # fmt: skip
    svc = InvestigationService.create(get_settings().test_database_url, deps=deps)
    yield svc
    svc.close()


@pytest.fixture
def upload(clean_db):
    """Intake an invoice (fake extractor) and return its investigation id."""

    def _upload(document: str = "# invoice", **overrides: Any) -> str:
        ex = extracted(**overrides)
        with clean_db() as s, s.begin():
            return intake_document(s, f"{ex.invoice_number}.md", document.encode(), "Thandi Nkosi",
                                   extractor=lambda _: ex).investigation_id  # fmt: skip

    return _upload
