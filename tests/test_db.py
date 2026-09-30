import pytest
from sqlalchemy import func, select, text
from sqlalchemy.exc import DBAPIError

from trustagent.config import get_settings
from trustagent.db.models import AuditLogRow, PolicyRow, SupplierRow, TransactionRow
from trustagent.db.seed import seed
from trustagent.db.session import EmbeddingDimensionMismatch, check_embedding_dimension


def test_seed_inserts_reference_data_and_is_idempotent(db_session):
    first = seed(db_session)
    assert first == {"suppliers": 3, "transactions": 6, "policies": 4}

    second = seed(db_session)
    assert second == {"suppliers": 0, "transactions": 0, "policies": 0}

    assert db_session.scalar(select(func.count()).select_from(SupplierRow)) == 3
    assert db_session.scalar(select(func.count()).select_from(TransactionRow)) == 6
    assert db_session.scalar(select(func.count()).select_from(PolicyRow)) == 4


def test_seed_does_not_overwrite_edited_suppliers(db_session):
    seed(db_session)
    supplier = db_session.get(SupplierRow, "SUP-003")
    supplier.bank_account = "****9999"
    db_session.flush()

    seed(db_session)
    db_session.refresh(supplier)
    assert supplier.bank_account == "****9999"


def test_seed_suppliers_match_reference(db_session):
    seed(db_session)
    abc = db_session.get(SupplierRow, "SUP-001")
    assert (abc.bank_account, abc.bank_name) == ("****4821", "First National Bank")
    assert (abc.expected_spend_min, abc.expected_spend_max) == (15000, 40000)
    digital = db_session.get(SupplierRow, "SUP-003")
    assert digital.expected_spend_min is None and digital.expected_spend_max is None


def test_audit_log_is_append_only(db_session):
    entry = AuditLogRow(actor="system", action="test", detail="original")
    db_session.add(entry)
    db_session.flush()

    with pytest.raises(DBAPIError, match="append-only"), db_session.begin_nested():
        db_session.execute(text("UPDATE audit_log SET detail = 'tampered'"))

    with pytest.raises(DBAPIError, match="append-only"), db_session.begin_nested():
        db_session.execute(text("DELETE FROM audit_log"))


def test_embedding_column_matches_config(db_engine):
    assert check_embedding_dimension(db_engine) == get_settings().embedding_dim


def test_embedding_dimension_mismatch_fails_fast(db_engine, monkeypatch):
    monkeypatch.setattr(get_settings(), "embedding_dim", 1536)
    with pytest.raises(EmbeddingDimensionMismatch, match=r"vector\(768\)"):
        check_embedding_dimension(db_engine)
