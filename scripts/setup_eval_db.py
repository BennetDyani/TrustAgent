"""Create the evaluation database (``trustagent_eval``) from the demo database, without re-embedding.

Run with:  uv run python scripts/setup_eval_db.py

The evaluation resets its database before every case, so it gets its own database and never touches the
demo. It needs the same schema and the same embedded contract and policy chunks as the demo database, so
run ``python -m trustagent.rag.ingest`` on the demo database first. The chunks are then *copied* (embeddings
included): no embedding calls, no quota. Safe to run again: it only copies when the chunks differ.
"""

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session

from trustagent.config import PROJECT_ROOT, get_settings
from trustagent.db.models import DocumentChunkRow

COPIED = [c.key for c in DocumentChunkRow.__table__.columns if c.computed is None and c.key != "id"]


def main() -> None:
    settings = get_settings()
    source_url, eval_url = settings.database_url, settings.eval_database_url
    name = make_url(eval_url).database

    admin = create_engine(source_url, isolation_level="AUTOCOMMIT")
    with admin.connect() as conn:
        if not conn.execute(text("SELECT 1 FROM pg_database WHERE datname = :n"), {"n": name}).scalar():
            conn.execute(text(f'CREATE DATABASE "{name}"'))
            print(f"created database {name}")
    admin.dispose()

    target = create_engine(eval_url)
    with target.begin() as conn:
        conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
    cfg = Config(str(PROJECT_ROOT / "alembic.ini"))
    cfg.set_main_option("sqlalchemy.url", eval_url)
    command.upgrade(cfg, "head")

    source = create_engine(source_url)
    with Session(source) as src:
        rows = src.scalars(select(DocumentChunkRow).order_by(DocumentChunkRow.id)).all()
        chunks = [{k: getattr(r, k) for k in COPIED} for r in rows]
    if not chunks:
        raise SystemExit("The demo database has no document chunks: run `python -m trustagent.rag.ingest` first.")

    with Session(target) as dst, dst.begin():
        existing = dst.scalar(select(text("count(*)")).select_from(DocumentChunkRow))
        if existing == len(chunks):
            print(f"{name} already has the {existing} document chunks; nothing to copy.")
        else:
            dst.execute(text("TRUNCATE document_chunks RESTART IDENTITY"))
            dst.execute(DocumentChunkRow.__table__.insert(), chunks)
            print(f"copied {len(chunks)} document chunks (with embeddings) into {name}")
    source.dispose()
    target.dispose()
    print("Evaluation database ready: uv run python -m trustagent.evaluation.run --offline")


if __name__ == "__main__":
    main()
