"""FastAPI: the contract for a UI, an ERP bot or an n8n flow.

- **Identity (bug #8).** Who is acting comes only from the ``X-Acting-As`` header, looked up server-side
  in ``workflow.identity``. Names or roles in a request body are never read. Production: SSO/OIDC claims.
- **No blocking in async (bug #9).** Every endpoint is a plain ``def``, which FastAPI runs in a thread pool.
  SSE streams are sync generators, which Starlette iterates in a thread pool too. A test checks this.
- **Progress over SSE.** ``event: <type>`` / ``data: <json>``; evidence always precedes the score.
- **Errors in one place.** GuardError 409, NotFound 404, unknown user 401, bad verification 422.

Run with:  uv run uvicorn trustagent.api.app:app --reload
"""

import datetime as dt
import json
from collections.abc import Iterator
from contextlib import asynccontextmanager
from typing import Annotated, Any

from fastapi import Depends, FastAPI, File, Header, Request, UploadFile
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, ConfigDict
from sqlalchemy import func, select, text

from trustagent.config import get_settings
from trustagent.db.models import DocumentChunkRow, SupplierRow
from trustagent.db.session import check_embedding_dimension
from trustagent.domain import Approver, HumanAction
from trustagent.workflow import queries
from trustagent.workflow.guards import GuardError
from trustagent.workflow.identity import DEMO_USERS, UnknownUser, resolve_acting_as
from trustagent.workflow.intake import IntakeRejected, intake_document
from trustagent.workflow.service import InvestigationService, NotFound
from trustagent.workflow.verification import VerificationEvidence, VerificationRejected, apply_supplier_verification


def _event(event: dict) -> str:
    return f"event: {event['event']}\ndata: {json.dumps(event, default=queries.json_default)}\n\n"


class ActionRequest(BaseModel):
    """Only the action and an optional note: the approver is the authenticated user, never the body.

    Extra fields are rejected (422), so a client can't even try to send an "approver" or a role.
    """

    model_config = ConfigDict(extra="forbid")

    action: HumanAction
    note: str | None = None


def create_app(service: InvestigationService | None = None) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        svc = service or InvestigationService.create()
        check_embedding_dimension(svc.deps.session_factory.kw["bind"])  # fail fast on a model/column mismatch
        app.state.service = svc
        yield
        if service is None:
            svc.close()

    app = FastAPI(title="TrustAgent API", version="1.0", lifespan=lifespan)

    # --- plumbing ------------------------------------------------------------------------------

    def svc(request: Request) -> InvestigationService:
        return request.app.state.service

    def current_user(x_acting_as: Annotated[str | None, Header()] = None) -> Approver:
        return resolve_acting_as(x_acting_as)

    Service = Annotated[InvestigationService, Depends(svc)]
    User = Annotated[Approver, Depends(current_user)]

    def respond(body: Any, status: int = 200) -> JSONResponse:
        return JSONResponse(json.loads(json.dumps(body, default=queries.json_default)), status_code=status)

    for exc_type, status in ((GuardError, 409), (NotFound, 404), (UnknownUser, 401), (VerificationRejected, 422)):

        def handler(_request: Request, exc: Exception, status: int = status) -> JSONResponse:
            return JSONResponse({"detail": str(exc)}, status_code=status)

        app.add_exception_handler(exc_type, handler)

    @app.exception_handler(IntakeRejected)
    def intake_rejected(_request: Request, exc: IntakeRejected) -> JSONResponse:
        return JSONResponse({"detail": exc.message}, status_code=exc.status_code)

    def sse(events: Iterator[dict]) -> StreamingResponse:
        """Prime the generator first, so a refused start is a 409, not a 200 with an error inside."""
        try:
            first = next(events)
        except StopIteration:
            first = None

        def body() -> Iterator[str]:
            if first is not None:
                yield _event(first)
            for event in events:
                yield _event(event)

        return StreamingResponse(body(), media_type="text/event-stream", headers={"Cache-Control": "no-cache"})

    # --- endpoints -------------------------------------------------------------------------------

    @app.get("/health")
    def health(s: Service) -> JSONResponse:
        with s.deps.session_factory() as db:
            db.execute(text("SELECT 1"))
            chunks = db.scalar(select(func.count()).select_from(DocumentChunkRow))
        st = get_settings()
        return respond(
            {
                "status": "ok",
                "llm": f"{st.llm_provider}/{st.llm_model}",
                "embedding": f"{st.embedding_model}/{st.embedding_dim}",
                "document_chunks": chunks,
            }
        )

    @app.get("/users")
    def users() -> JSONResponse:
        """Demo identities for the 'Acting as' switcher. Production: SSO."""
        return respond([{"key": k, "name": u.name, "role": u.role} for k, u in DEMO_USERS.items()])

    @app.get("/me")
    def me(user: User) -> JSONResponse:
        return respond(user.model_dump())

    @app.post("/invoices", status_code=201)
    def upload_invoice(s: Service, user: User, file: Annotated[UploadFile, File()]) -> JSONResponse:
        content = file.file.read()
        with s.deps.session_factory() as db, db.begin():
            result = intake_document(db, file.filename or "upload", content, submitted_by=user.name)
        return respond({
            "investigation_id": result.investigation_id, "supplier_id": result.supplier_id,
            "supplier_match": result.supplier_match, "match_confidence": round(result.match_confidence, 2),
            "method": result.method, "warnings": result.warnings,
            "invoice": result.invoice.model_dump(mode="json"),
        }, 201)  # fmt: skip

    @app.get("/investigations")
    def list_investigations(s: Service, user: User) -> JSONResponse:
        with s.deps.session_factory() as db:
            return respond(queries.list_cases(db))

    @app.get("/investigations/{investigation_id}")
    def get_investigation(investigation_id: str, s: Service, user: User) -> JSONResponse:
        with s.deps.session_factory() as db:
            detail = queries.case_detail(db, investigation_id, user)
        if detail is None:
            raise NotFound(f"Investigation {investigation_id} not found.")
        return respond(detail)

    @app.post("/investigations/{investigation_id}/run")
    def run_investigation(investigation_id: str, s: Service, user: User) -> StreamingResponse:
        return sse(s.start(investigation_id, user))

    @app.post("/investigations/{investigation_id}/rerun")
    def rerun_investigation(investigation_id: str, s: Service, user: User) -> StreamingResponse:
        return sse(s.rerun(investigation_id, user))

    @app.post("/investigations/{investigation_id}/recover")
    def recover_investigation(investigation_id: str, s: Service, user: User) -> StreamingResponse:
        return sse(s.recover(investigation_id))

    @app.post("/investigations/{investigation_id}/actions")
    def act(investigation_id: str, body: ActionRequest, s: Service, user: User) -> JSONResponse:
        result = s.act(investigation_id, body.action, user, body.note)
        return respond({"ok": result.ok, "message": result.message, "closed": result.closed},
                       200 if result.ok else result.status_code)  # fmt: skip

    @app.get("/suppliers")
    def suppliers(s: Service, user: User) -> JSONResponse:
        with s.deps.session_factory() as db:
            return respond(queries.list_suppliers(db))

    @app.post("/suppliers/{supplier_id}/verify")
    def verify_supplier(supplier_id: str, body: VerificationEvidence, s: Service, user: User) -> JSONResponse:
        with s.deps.session_factory() as db, db.begin():
            if db.get(SupplierRow, supplier_id) is None:
                raise NotFound(f"Supplier {supplier_id} not found.")
            outcome = apply_supplier_verification(db, supplier_id, body, user, dt.date.today())
        return respond({"supplier_id": outcome.supplier_id, "cases_verified": outcome.cases_verified,
                        "cases_mismatched": outcome.cases_mismatched})  # fmt: skip

    return app


app = create_app()  # the lifespan (database pool, model clients) only starts when served
