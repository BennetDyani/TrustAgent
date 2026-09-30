"""Reset the DEMO database to a clean starting point (keeps the ingested documents).

Run with:  uv run python scripts/reset_demo.py [--with-samples]

- Removes all cases, invoices, audit entries and checkpoints, and restores the seed suppliers,
  transactions and policies. Document chunks are kept, so nothing needs re-embedding.
- ``--with-samples`` uploads the five JSON sample invoices (read by code, no model calls), leaving
  them PENDING so an investigation can be run live.

Demo tooling only: it uses TRUNCATE, which the audit log's append-only trigger does not block (see
DECISIONS.md). A production database role would not be able to do this.
"""

import sys

from sqlalchemy import text

from trustagent.config import get_settings
from trustagent.db.seed import seed
from trustagent.db.session import get_engine, session_scope
from trustagent.workflow.intake import intake_document

APP_TABLES = "audit_log, evidence, investigations, invoices, transactions, suppliers, policies"


def main() -> None:
    engine = get_engine()
    with engine.begin() as conn:
        conn.execute(text(f"TRUNCATE {APP_TABLES} RESTART IDENTITY CASCADE"))
        for table in ("checkpoints", "checkpoint_blobs", "checkpoint_writes"):
            if conn.execute(text("SELECT to_regclass(:t)"), {"t": table}).scalar():
                conn.execute(text(f"TRUNCATE {table}"))
    with session_scope() as session:
        seed(session)
    print("Demo database reset: seed suppliers, transactions and policies restored; documents kept.")

    if "--with-samples" in sys.argv:
        invoices = get_settings().invoices_dir
        for path in sorted(invoices.glob("*.json")):
            if path.name == "labels.json":
                continue
            with session_scope() as session:
                r = intake_document(session, path.name, path.read_bytes(), submitted_by="demo setup")
            print(f"  {path.name}: {r.investigation_id} ({r.supplier_match})")


if __name__ == "__main__":
    main()
