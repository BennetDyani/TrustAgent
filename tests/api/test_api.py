"""API contract tests: identity, SSE ordering, evidence before decisions, status codes, no blocking endpoints."""

import inspect
import json

import pytest
from fastapi.testclient import TestClient

from tests.graph.conftest import clean_db, fake_llm, service  # noqa: F401  (fixtures)
from trustagent.api.app import create_app
from trustagent.config import get_settings

THANDI, SIPHO, LERATO = ({"X-Acting-As": k} for k in ("thandi", "sipho", "lerato"))
INVOICES = get_settings().invoices_dir


@pytest.fixture
def api(service):  # noqa: F811
    with TestClient(create_app(service)) as client:
        yield client


def upload(api, name, as_=THANDI):
    with open(INVOICES / name, "rb") as f:
        return api.post("/invoices", files={"file": (name, f, "application/json")}, headers=as_)


def events(response) -> list[dict]:
    out, name = [], None
    for line in response.text.splitlines():
        if line.startswith("event: "):
            name = line.removeprefix("event: ")
        elif line.startswith("data: "):
            data = json.loads(line.removeprefix("data: "))
            assert data["event"] == name
            out.append(data)
    return out


def run(api, case_id, as_=THANDI):
    return api.post(f"/investigations/{case_id}/run", headers=as_)


# --- identity (bug #8) ---------------------------------------------------------------------------


@pytest.mark.parametrize("headers", [{}, {"X-Acting-As": "mallory"}])
def test_unknown_or_missing_identity_is_401(api, headers):
    assert api.get("/investigations", headers=headers).status_code == 401


def test_the_body_cannot_name_the_approver(api):
    case_id = upload(api, "INV-1049-metro-cleaning.json").json()["investigation_id"]
    run(api, case_id)
    body = {"action": "APPROVE_PAYMENT", "approver": {"name": "Sipho Dlamini", "role": "FINANCE_MANAGER"}}
    assert api.post(f"/investigations/{case_id}/actions", json=body, headers=THANDI).status_code == 422


def test_me_reflects_the_header(api):
    assert api.get("/me", headers=LERATO).json() == {"name": "Lerato Khumalo", "role": "DEPARTMENT_HEAD"}


# --- upload ------------------------------------------------------------------------------------------


def test_upload_creates_a_pending_case(api):
    r = upload(api, "INV-1049-metro-cleaning.json")
    assert r.status_code == 201
    body = r.json()
    assert body["supplier_match"] == "MATCHED_EXISTING" and body["method"] == "json"
    assert body["invoice"]["bank_account"] == "****7733"
    detail = api.get(f"/investigations/{body['investigation_id']}", headers=THANDI).json()
    assert detail["status"] == "PENDING"


def test_upload_errors_have_the_right_status(api):
    assert upload(api, "INV-1049-metro-cleaning.json").status_code == 201
    assert upload(api, "INV-1049-metro-cleaning.json").status_code == 409  # same number, same supplier
    r = api.post("/invoices", files={"file": ("x.docx", b"data", "application/octet-stream")}, headers=THANDI)
    assert r.status_code == 422 and "Unsupported" in r.json()["detail"]


# --- running: SSE, ordering, evidence before decisions ---------------------------------------------------


def test_run_streams_evidence_before_score_then_pauses(api):
    case_id = upload(api, "INV-1048-abc-office-solutions.json").json()["investigation_id"]
    before = api.get(f"/investigations/{case_id}", headers=THANDI).json()
    assert before["actions"] == []  # no decision options before the investigation has run

    r = run(api, case_id)
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/event-stream")
    names = [e["event"] for e in events(r)]
    assert max(i for i, n in enumerate(names) if n == "evidence") < names.index("risk") < names.index("recommendation")
    assert names[-1] == "awaiting_decision"

    after = api.get(f"/investigations/{case_id}", headers=THANDI).json()
    assert after["status"] == "ACTION_REQUIRED" and after["evidence"]
    assert {a["action"] for a in after["actions"]} == {"APPROVE_PAYMENT", "REQUEST_VERIFICATION", "ESCALATE",
                                                      "HOLD_PAYMENT", "REJECT_INVOICE"}  # fmt: skip


def test_a_second_run_is_a_409_not_a_stream(api):
    case_id = upload(api, "INV-1049-metro-cleaning.json").json()["investigation_id"]
    run(api, case_id)
    r = run(api, case_id)
    assert r.status_code == 409 and "PENDING" in r.json()["detail"]


def test_unknown_case_is_404(api):
    assert api.get("/investigations/CASE-NOPE", headers=THANDI).status_code == 404
    assert run(api, "CASE-NOPE").status_code == 404


# --- actions ----------------------------------------------------------------------------------------------


def test_blocked_actions_explain_why_and_the_server_still_refuses(api):
    # INV-1048: bank change on a verified supplier -> HOLD; approval blocked until verified.
    case_id = upload(api, "INV-1048-abc-office-solutions.json").json()["investigation_id"]
    run(api, case_id)
    approve = next(a for a in api.get(f"/investigations/{case_id}", headers=SIPHO).json()["actions"]
                   if a["action"] == "APPROVE_PAYMENT")  # fmt: skip
    assert not approve["allowed"] and "phone and email" in approve["reason"]

    r = api.post(f"/investigations/{case_id}/actions", json={"action": "APPROVE_PAYMENT"}, headers=SIPHO)
    assert r.status_code == 409 and not r.json()["ok"]
    hold = api.post(f"/investigations/{case_id}/actions", json={"action": "HOLD_PAYMENT"}, headers=THANDI)
    assert hold.status_code == 200 and hold.json()["ok"]


def test_dual_approval_through_the_api(api):
    # INV-1051: R106,950, so POL-002 needs a Finance Manager AND a Department Head.
    case_id = upload(api, "INV-1051-secure-it-solutions.json").json()["investigation_id"]
    run(api, case_id)

    def approve(who):
        return api.post(f"/investigations/{case_id}/actions", json={"action": "APPROVE_PAYMENT"}, headers=who)

    assert approve(THANDI).status_code == 403
    first = approve(SIPHO)
    assert first.status_code == 200 and not first.json()["closed"] and "Department Head" in first.json()["message"]
    assert approve(LERATO).json()["closed"]
    assert api.get(f"/investigations/{case_id}", headers=THANDI).json()["status"] == "CLOSED"


# --- supplier verification --------------------------------------------------------------------------------


EVIDENCE = {
    "phone_confirmed": True, "phone_contact": "+27 11 555 0192", "phone_source": "onboarding_record",
    "email_confirmed": True, "email_contact": "accounts@abcoffice.co.za", "email_source": "onboarding_record",
    "confirmed_bank_account": "1092847591 9917",
}  # fmt: skip


def test_verification_from_invoice_contacts_is_refused(api):
    r = api.post("/suppliers/SUP-001/verify", json={**EVIDENCE, "phone_source": "invoice"}, headers=SIPHO)
    assert r.status_code == 422 and "never from the invoice" in r.json()["detail"]


def test_verification_clears_matching_open_cases(api):
    case_id = upload(api, "INV-1048-abc-office-solutions.json").json()["investigation_id"]
    run(api, case_id)
    r = api.post("/suppliers/SUP-001/verify", json=EVIDENCE, headers=SIPHO)
    assert r.status_code == 200 and r.json()["cases_verified"] == [case_id]
    supplier = next(s for s in api.get("/suppliers", headers=SIPHO).json() if s["id"] == "SUP-001")
    assert supplier["verified_by"] == "Sipho Dlamini" and supplier["bank_account"] == "****9917"


# --- structure ----------------------------------------------------------------------------------------------


def test_no_endpoint_blocks_the_event_loop(api):
    """Bug #9: every endpoint is a plain def (run in a thread pool), never async def calling sync code."""
    endpoints = [r.endpoint for r in api.app.routes if getattr(r, "methods", None) and r.path.startswith("/")]
    ours = [e for e in endpoints if e.__module__ == "trustagent.api.app"]
    assert ours and not [e.__name__ for e in ours if inspect.iscoroutinefunction(e)]


def test_health(api):
    body = api.get("/health").json()
    assert body["status"] == "ok" and body["embedding"].endswith("/768")
