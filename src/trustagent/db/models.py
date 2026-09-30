"""SQLAlchemy 2 ORM models.

Postgres is the system of record for cases, evidence and the audit log. The
LangGraph checkpoint only holds the state of a paused run (ADR-006).
LangGraph creates its own checkpoint tables via ``PostgresSaver.setup()``;
they are not managed by Alembic.
"""

import datetime as dt
from decimal import Decimal
from typing import Any

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    BigInteger,
    Boolean,
    Computed,
    Date,
    DateTime,
    ForeignKey,
    Identity,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, TSVECTOR
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from trustagent.config import get_settings

Money = Numeric(14, 2)


class Base(DeclarativeBase):
    pass


class SupplierRow(Base):
    __tablename__ = "suppliers"

    id: Mapped[str] = mapped_column(String(20), primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    contact_email: Mapped[str | None] = mapped_column(String(200))
    bank_account: Mapped[str] = mapped_column(String(20))  # masked: ****1234
    bank_name: Mapped[str | None] = mapped_column(String(100))
    registration_number: Mapped[str | None] = mapped_column(String(50))
    risk_status: Mapped[str] = mapped_column(String(10), default="LOW")
    verified: Mapped[bool] = mapped_column(Boolean, default=False)
    verified_date: Mapped[dt.date | None] = mapped_column(Date)
    verified_by: Mapped[str | None] = mapped_column(String(200))
    expected_spend_min: Mapped[Decimal | None] = mapped_column(Money)
    expected_spend_max: Mapped[Decimal | None] = mapped_column(Money)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class TransactionRow(Base):
    __tablename__ = "transactions"

    id: Mapped[str] = mapped_column(String(20), primary_key=True)
    supplier_id: Mapped[str] = mapped_column(ForeignKey("suppliers.id"), index=True)
    invoice_id: Mapped[str | None] = mapped_column(String(50))
    amount: Mapped[Decimal] = mapped_column(Money)
    currency: Mapped[str] = mapped_column(String(3), default="ZAR")
    bank_account: Mapped[str] = mapped_column(String(20))
    date: Mapped[dt.date] = mapped_column(Date)
    status: Mapped[str] = mapped_column(String(20))
    description: Mapped[str] = mapped_column(Text, default="")


class PolicyRow(Base):
    __tablename__ = "policies"

    id: Mapped[str] = mapped_column(String(20), primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    category: Mapped[str] = mapped_column(String(20))
    rule: Mapped[str] = mapped_column(Text)
    description: Mapped[str] = mapped_column(Text)
    severity: Mapped[str] = mapped_column(String(10))
    action: Mapped[str] = mapped_column(Text)


class InvoiceRow(Base):
    __tablename__ = "invoices"

    id: Mapped[str] = mapped_column(String(50), primary_key=True)
    supplier_id: Mapped[str | None] = mapped_column(ForeignKey("suppliers.id"), index=True)
    supplier_name: Mapped[str] = mapped_column(String(200))
    amount: Mapped[Decimal] = mapped_column(Money)
    currency: Mapped[str] = mapped_column(String(3), default="ZAR")
    date: Mapped[dt.date | None] = mapped_column(Date)
    due_date: Mapped[dt.date | None] = mapped_column(Date)
    bank_account: Mapped[str] = mapped_column(String(20))  # masked
    bank_name: Mapped[str | None] = mapped_column(String(100))
    bank_account_holder: Mapped[str | None] = mapped_column(String(200))
    supplier_email: Mapped[str | None] = mapped_column(String(200))
    description: Mapped[str] = mapped_column(Text, default="")
    line_items: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, default=list)
    status: Mapped[str] = mapped_column(String(20), default="SUBMITTED")
    urgency: Mapped[str] = mapped_column(String(10), default="NORMAL")
    source_filename: Mapped[str | None] = mapped_column(String(300))
    # Untrusted document text, kept so the case can be re-run and audited.
    raw_text: Mapped[str | None] = mapped_column(Text)
    extraction_warnings: Mapped[list[str]] = mapped_column(JSONB, default=list)
    submitted_by: Mapped[str | None] = mapped_column(String(200))
    # Upload order. Duplicate checks compare only with EARLIER uploads, so the first
    # submission is the original and a later copy is the duplicate (ADR-026).
    # created_at can't be used: now() is identical for every row in one transaction.
    upload_seq: Mapped[int] = mapped_column(BigInteger, Identity())

    __table_args__ = (UniqueConstraint("upload_seq", name="uq_invoices_upload_seq"),)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class InvestigationRow(Base):
    __tablename__ = "investigations"

    id: Mapped[str] = mapped_column(String(50), primary_key=True)
    invoice_id: Mapped[str] = mapped_column(ForeignKey("invoices.id"), index=True)
    supplier_id: Mapped[str | None] = mapped_column(ForeignKey("suppliers.id"), index=True)
    status: Mapped[str] = mapped_column(String(20), default="PENDING", index=True)
    # Each re-run gets a new LangGraph thread: "{id}:run-{run_number}".
    run_number: Mapped[int] = mapped_column(Integer, default=0)
    risk_score: Mapped[int | None] = mapped_column(Integer)
    risk_level: Mapped[str | None] = mapped_column(String(10))
    summary: Mapped[str | None] = mapped_column(Text)
    recommendation: Mapped[str | None] = mapped_column(Text)
    recommended_action: Mapped[str | None] = mapped_column(String(30))
    decision: Mapped[str | None] = mapped_column(String(30))
    ai_review_available: Mapped[bool | None] = mapped_column(Boolean)
    deep_dive_summary: Mapped[str | None] = mapped_column(Text)
    # One entry per model call in the current run: step, model, input/output tokens (cost accounting).
    llm_usage: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, default=list, server_default="[]")
    approvals: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, default=list)
    verification: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class EvidenceRow(Base):
    __tablename__ = "evidence"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    investigation_id: Mapped[str] = mapped_column(ForeignKey("investigations.id"), index=True)
    run_number: Mapped[int] = mapped_column(Integer)
    indicator_type: Mapped[str] = mapped_column(String(40))
    description: Mapped[str] = mapped_column(Text)
    severity: Mapped[str] = mapped_column(String(10))
    source: Mapped[str] = mapped_column(String(10))  # RULE | AI | CONTRACT
    weight: Mapped[int] = mapped_column(Integer)
    citations: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, default=list)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class AuditLogRow(Base):
    """Append-only. Rows are never updated or deleted, including on re-run."""

    __tablename__ = "audit_log"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    investigation_id: Mapped[str | None] = mapped_column(String(50), index=True)
    timestamp: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    actor: Mapped[str] = mapped_column(String(200))  # "system", "agent" or a named person
    action: Mapped[str] = mapped_column(String(200))
    detail: Mapped[str] = mapped_column(Text, default="")
    tool_used: Mapped[str | None] = mapped_column(String(100))
    status: Mapped[str] = mapped_column(String(20), default="COMPLETED")


class DocumentChunkRow(Base):
    """RAG chunks from contracts and the policy manual, with metadata for filtering."""

    __tablename__ = "document_chunks"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    document_id: Mapped[str] = mapped_column(String(100), index=True)
    source: Mapped[str] = mapped_column(String(300))  # file name, for citations
    doc_type: Mapped[str] = mapped_column(String(20), index=True)  # contract | policy
    supplier_id: Mapped[str | None] = mapped_column(String(20), index=True)
    contract_id: Mapped[str | None] = mapped_column(String(50))
    section: Mapped[str | None] = mapped_column(String(300))
    page: Mapped[int | None] = mapped_column(Integer)
    effective_from: Mapped[dt.date | None] = mapped_column(Date)
    effective_to: Mapped[dt.date | None] = mapped_column(Date)
    chunk_index: Mapped[int] = mapped_column(Integer)
    content: Mapped[str] = mapped_column(Text)
    embedding_model: Mapped[str] = mapped_column(String(100))
    # Dimension comes from config; start-up verifies it matches the live column.
    embedding: Mapped[list[float]] = mapped_column(Vector(get_settings().embedding_dim))
    # Keyword index is persistent in Postgres, not rebuilt in memory on start-up.
    content_tsv: Mapped[str] = mapped_column(TSVECTOR, Computed("to_tsvector('english', content)", persisted=True))

    __table_args__ = (
        Index("ix_document_chunks_tsv", "content_tsv", postgresql_using="gin"),
        Index(
            "ix_document_chunks_embedding",
            "embedding",
            postgresql_using="hnsw",
            postgresql_ops={"embedding": "vector_cosine_ops"},
        ),
    )
