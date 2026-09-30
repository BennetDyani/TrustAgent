"""A small HTTP client for the TrustAgent API: the UI's only way in.

The UI never imports the workflow directly; it is a client of the same API an ERP
bot or n8n flow would use. Identity travels as the ``X-Acting-As`` header.
"""

import json
from collections.abc import Iterator
from typing import Any

import httpx

from trustagent.config import get_settings


class ApiError(Exception):
    def __init__(self, status: int, detail: str):
        super().__init__(detail)
        self.status, self.detail = status, detail


class ApiClient:
    def __init__(
        self, user_key: str, base_url: str | None = None, timeout: float = 60.0, http: httpx.Client | None = None
    ):
        # ``http`` lets tests pass FastAPI's TestClient (an httpx.Client) to run the UI against the real API in-process.
        self.base_url = (base_url or get_settings().api_base_url).rstrip("/") if http is None else ""
        self.headers = {"X-Acting-As": user_key}
        # No read timeout: an investigation stream can run for a minute or more on a paced free tier.
        self._http = http or httpx.Client(timeout=httpx.Timeout(timeout, read=None))

    def _call(self, method: str, path: str, **kwargs: Any) -> Any:
        r = self._http.request(method, self.base_url + path, headers=self.headers, **kwargs)
        body = r.json() if r.headers.get("content-type", "").startswith("application/json") else {}
        if r.status_code >= 400 and not (isinstance(body, dict) and "ok" in body):
            raise ApiError(r.status_code, body.get("detail", r.text) if isinstance(body, dict) else r.text)
        return body

    # --- reads ---
    def health(self) -> dict:
        return self._call("GET", "/health")

    def users(self) -> list[dict]:
        return self._call("GET", "/users")

    def cases(self) -> dict:
        return self._call("GET", "/investigations")

    def case(self, case_id: str) -> dict:
        return self._call("GET", f"/investigations/{case_id}")

    def suppliers(self) -> list[dict]:
        return self._call("GET", "/suppliers")

    # --- writes ---
    def upload(self, filename: str, content: bytes) -> dict:
        return self._call("POST", "/invoices", files={"file": (filename, content)})

    def act(self, case_id: str, action: str, note: str | None = None) -> dict:
        return self._call("POST", f"/investigations/{case_id}/actions", json={"action": action, "note": note})

    def verify(self, supplier_id: str, evidence: dict) -> dict:
        return self._call("POST", f"/suppliers/{supplier_id}/verify", json=evidence)

    def stream(self, case_id: str, operation: str = "run") -> Iterator[dict]:
        """POST /investigations/{id}/{run|rerun|recover} and yield SSE events as they arrive."""
        url = f"{self.base_url}/investigations/{case_id}/{operation}"
        with self._http.stream("POST", url, headers=self.headers) as r:
            if r.status_code >= 400:
                r.read()
                raise ApiError(r.status_code, r.json().get("detail", r.text))
            for line in r.iter_lines():
                if line.startswith("data: "):
                    yield json.loads(line.removeprefix("data: "))
