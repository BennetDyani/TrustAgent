"""Engine and session factory, plus the start-up embedding-dimension check."""

from collections.abc import Iterator
from contextlib import contextmanager
from functools import lru_cache

from sqlalchemy import Engine, create_engine, text
from sqlalchemy.orm import Session, sessionmaker

from trustagent.config import get_settings


@lru_cache
def get_engine(url: str | None = None) -> Engine:
    return create_engine(url or get_settings().database_url, pool_pre_ping=True)


def get_sessionmaker(url: str | None = None) -> sessionmaker[Session]:
    return sessionmaker(bind=get_engine(url), expire_on_commit=False)


@contextmanager
def session_scope(url: str | None = None) -> Iterator[Session]:
    """A transaction: commit on success, roll back on any exception."""
    session = get_sessionmaker(url)()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


class EmbeddingDimensionMismatch(RuntimeError):
    pass


def check_embedding_dimension(engine: Engine) -> int:
    """Fail fast if the pgvector column and the configured model disagree.

    A mismatched column once broke all ingestion in a previous project; this
    turns that into a clear start-up error instead.
    """
    expected = get_settings().embedding_dim
    with engine.connect() as conn:
        # For the vector type, atttypmod holds the declared dimension.
        actual = conn.execute(
            text(
                "SELECT atttypmod FROM pg_attribute "
                "WHERE attrelid = 'document_chunks'::regclass AND attname = 'embedding'"
            )
        ).scalar_one()
    if actual != expected:
        raise EmbeddingDimensionMismatch(
            f"document_chunks.embedding is vector({actual}) but EMBEDDING_DIM={expected} "
            f"({get_settings().embedding_model}). Create a migration to change the column "
            "and re-ingest."
        )
    return actual
