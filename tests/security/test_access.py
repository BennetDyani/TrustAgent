"""Authorisation, least privilege and POPIA, tested from the outside (the API) where an attacker would be."""

import json
import logging
import re

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from tests.api.test_api import EVIDENCE, INVOICES, LERATO, SIPHO, THANDI, events, run, upload
from tests.graph.conftest import clean_db, fake_llm, service  # noqa: F401  (fixtures)
from trustagent.api.app import create_app
from trustagent.graph.deep_dive import SUBMIT, DeepDiveInput, build_tools

PUBLIC = {"/health", "/users", "/openapi.json", "/docs", "/docs/oauth2-redirect", "/redoc"}


@pytest.fixture
def api(service):  # noqa: F811
    with TestClient(create_app(service)) as client:
        yield client


def act(api, case_id, action, who):
    return api.post(f"/investigations/{case_id}/actions", json={"action": action}, headers=who)


# --- identity: every endpoint, including ones added later ---------------------------------------------


def test_every_non_public_endpoint_requires_an_identity(api):
    routes = [(m, r.path) for r in api.app.routes if r.path not in PUBLIC for m in getattr(r, "methods", ()) or ()]
    assert len(routes) >= 10  # the sweep really found the endpoints
    for method, path in routes:
        r = api.request(method, path.replace("{investigation_id}", "CASE-X").replace("{supplier_id}", "SUP-001"))
        assert r.status_code == 401, f"{method} {path} answered {r.status_code} without an identity"


@pytest.mark.parametrize("header", ["sipho,lerato", "../sipho", "sipho lerato", "", "SUP-001"])
def test_near_miss_identities_are_refused(api, header):
    assert api.get("/me", headers={"X-Acting-As": header}).status_code == 401


@pytest.mark.parametrize("header", ["Sipho", " sipho ", "SIPHO"])
def test_case_and_spacing_resolve_to_the_same_person_never_another(api, header):
    assert api.get("/me", headers={"X-Acting-As": header}).json()["name"] == "Sipho Dlamini"


def test_the_verification_body_cannot_name_the_verifier(api):
    body = {**EVIDENCE, "verified_by": {"name": "Lerato Khumalo", "role": "DEPARTMENT_HEAD"}}
    assert api.post("/suppliers/SUP-001/verify", json=body, headers=THANDI).status_code == 422


# --- authorisation: who may approve -------------------------------------------------------------------


def test_verifier_cannot_approve_and_a_rerun_is_needed_first(api):
    # INV-1048: bank change -> HOLD. Sipho verifies by phone and email; Sipho may not also approve.
    case_id = upload(api, "INV-1048-abc-office-solutions.json").json()["investigation_id"]
    run(api, case_id)
    assert api.post("/suppliers/SUP-001/verify", json=EVIDENCE, headers=SIPHO).status_code == 200

    assert act(api, case_id, "APPROVE_PAYMENT", LERATO).status_code == 409  # re-run with verified data first
    api.post(f"/investigations/{case_id}/rerun", headers=THANDI)
    assert act(api, case_id, "APPROVE_PAYMENT", SIPHO).status_code == 403  # separation of duties
    assert act(api, case_id, "APPROVE_PAYMENT", LERATO).status_code == 200


# --- state guards ---------------------------------------------------------------------------------------


def test_no_decision_before_the_investigation_and_none_after_closing(api):
    case_id = upload(api, "INV-1049-metro-cleaning.json").json()["investigation_id"]
    assert act(api, case_id, "APPROVE_PAYMENT", LERATO).status_code == 409  # PENDING: no evidence yet
    run(api, case_id)
    assert act(api, case_id, "APPROVE_PAYMENT", LERATO).status_code == 200
    assert act(api, case_id, "REJECT_INVOICE", LERATO).status_code == 409  # closed
    assert api.post(f"/investigations/{case_id}/rerun", headers=LERATO).status_code == 409


# --- least privilege: the agent can look, never act -----------------------------------------------------


def test_the_agent_has_only_read_only_lookup_tools(clean_db):  # noqa: F811
    tools = build_tools(clean_db, DeepDiveInput("CASE-X", "INV-X", "SUP-001", "test"))
    assert {t.name for t in tools} == {"supplier_payment_history", "similar_invoices", "open_cases_for_supplier"}
    assert SUBMIT not in {t.name for t in tools}  # the terminal tool records findings; it changes nothing
    assert all(t.args == {} for t in tools)  # no ids from the model: every lookup is bound to this case


# --- POPIA: a full bank account number is never stored, shown, streamed or logged ------------------------

DB_TEXT = ["invoices", "investigations", "evidence", "audit_log", "suppliers", "transactions", "checkpoints"]
DB_BYTES = [("checkpoint_blobs", "blob"), ("checkpoint_writes", "blob")]


def test_full_account_number_never_leaves_intake(api, clean_db, caplog):  # noqa: F811
    # The shipped samples already hold masked numbers, so give one a full, realistic account number.
    invoice = json.loads((INVOICES / "INV-1048-abc-office-solutions.json").read_text(encoding="utf-8"))
    invoice["bank_details"]["account_number"] = full = "1092 847 591 9917"
    digits = full.replace(" ", "")

    with caplog.at_level(logging.DEBUG):
        posted = api.post("/invoices", files={"file": ("INV-1048.json", json.dumps(invoice), "application/json")},
                          headers=THANDI)  # fmt: skip
        assert posted.status_code == 201, posted.text
        case_id = posted.json()["investigation_id"]
        stream = run(api, case_id)
        detail = api.get(f"/investigations/{case_id}", headers=THANDI)

    def leaks(s: str) -> bool:  # however it is spaced or dashed
        return digits in re.sub(r"[\s-]", "", s)

    assert events(stream) and not leaks(stream.text), "SSE stream"
    assert not leaks(detail.text), "case detail"
    assert not leaks(caplog.text), "logs"
    with clean_db() as s:
        for table in DB_TEXT:
            rows = s.execute(text(f"SELECT t::text FROM {table} t")).scalars().all()
            assert rows or table in ("transactions",), f"{table} is empty: the check would prove nothing"
            assert not [r for r in rows if leaks(r)], f"full account number stored in {table}"
        for table, column in DB_BYTES:  # serialised graph state: search the bytes for both spellings
            found = f"SELECT count(*) FROM {table} WHERE position(convert_to(:n, 'UTF8') IN {column}) > 0"
            for spelling in (digits, full):
                hits = s.execute(text(found), {"n": spelling}).scalar()
                assert hits == 0, f"full account number in LangGraph {table}"
