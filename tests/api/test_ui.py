"""The UI against the real API, in-process: client calls, then a Streamlit AppTest render.

FastAPI's TestClient is an httpx.Client, so the UI's ApiClient can use it as its transport.
"""

import pytest
from fastapi.testclient import TestClient
from streamlit.testing.v1 import AppTest

from tests.api.test_api import INVOICES, api  # noqa: F401  (fixture)
from tests.graph.conftest import clean_db, fake_llm, service  # noqa: F401  (fixtures)
from trustagent.config import PROJECT_ROOT
from trustagent.ui import client as api_client

UI = str(PROJECT_ROOT / "src" / "trustagent" / "ui" / "app.py")


@pytest.fixture
def ui_client(api: TestClient):  # noqa: F811
    def make(user_key: str) -> api_client.ApiClient:
        return api_client.ApiClient(user_key, http=api)

    return make


def test_client_uploads_streams_and_acts(ui_client):
    thandi = ui_client("thandi")
    up = thandi.upload("INV-1049-metro-cleaning.json", (INVOICES / "INV-1049-metro-cleaning.json").read_bytes())
    case_id = up["investigation_id"]
    names = [e["event"] for e in thandi.stream(case_id)]
    assert names.index("risk") < names.index("awaiting_decision")
    assert thandi.act(case_id, "APPROVE_PAYMENT")["closed"]


def test_client_raises_api_errors_with_status(ui_client):
    with pytest.raises(api_client.ApiError) as exc:
        ui_client("thandi").case("CASE-NOPE")
    assert exc.value.status == 404
    with pytest.raises(api_client.ApiError) as exc:
        list(ui_client("thandi").stream("CASE-NOPE"))
    assert exc.value.status == 404


def test_refused_action_returns_the_reason_not_an_exception(ui_client):
    thandi = ui_client("thandi")
    case_id = thandi.upload("INV-1051.json", (INVOICES / "INV-1051-secure-it-solutions.json").read_bytes())[
        "investigation_id"
    ]
    list(thandi.stream(case_id))
    result = thandi.act(case_id, "APPROVE_PAYMENT")
    assert result["ok"] is False and "Finance Manager and a Department Head" in result["message"]


# --- Streamlit render -----------------------------------------------------------------------------------


@pytest.fixture
def app_test(api, monkeypatch):  # noqa: F811
    class InProcessClient(api_client.ApiClient):
        def __init__(self, user_key: str, **_):
            super().__init__(user_key, http=api)

    monkeypatch.setattr(api_client, "ApiClient", InProcessClient)
    return AppTest.from_file(UI, default_timeout=60)


def test_ui_renders_every_page_without_errors(app_test):
    at = app_test.run()
    assert not at.exception
    for page in ("Cases", "New invoice", "Suppliers"):
        at.radio(key="page").set_value(page).run()
        assert not at.exception, page


def test_case_page_shows_evidence_and_blocks_approval_with_the_reason(app_test, ui_client):
    thandi = ui_client("thandi")
    case_id = thandi.upload("INV-1048.json", (INVOICES / "INV-1048-abc-office-solutions.json").read_bytes())[
        "investigation_id"
    ]
    list(thandi.stream(case_id))

    at = app_test.run()
    at.radio(key="page").set_value("Cases").run()
    assert not at.exception
    approve = next(b for b in at.button if b.key == f"{case_id}-APPROVE_PAYMENT")
    hold = next(b for b in at.button if b.key == f"{case_id}-HOLD_PAYMENT")
    assert approve.disabled and not hold.disabled
    captions = " ".join(c.value for c in at.caption)
    assert "Approve payment" in captions and "phone and email" in captions
    assert any("BANK_DETAILS_CHANGED" in str(df.value) for df in at.dataframe)


def test_switching_user_changes_who_may_act(app_test, ui_client):
    thandi = ui_client("thandi")
    case_id = thandi.upload("INV-1051.json", (INVOICES / "INV-1051-secure-it-solutions.json").read_bytes())[
        "investigation_id"
    ]
    list(thandi.stream(case_id))

    at = app_test.run()
    at.radio(key="page").set_value("Cases").run()
    assert next(b for b in at.button if b.key == f"{case_id}-APPROVE_PAYMENT").disabled  # analyst, over R100k
    at.selectbox(key="user_key").set_value("sipho").run()
    assert not next(b for b in at.button if b.key == f"{case_id}-APPROVE_PAYMENT").disabled  # finance manager
