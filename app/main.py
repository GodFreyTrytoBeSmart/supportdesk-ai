"""FastAPI transport layer. Run on loopback only: this includes a login simulator."""

import logging
import sqlite3
import time
from contextlib import asynccontextmanager
from typing import Annotated
from uuid import uuid4

from fastapi import Depends, FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from app.auth import AuthContext, get_context, issue_session, require_reviewer
from app.config import Settings
from app.db import PERSONAS, Database
from app.intelligence import Intelligence
from app.knowledge import KnowledgeBase
from app.models import ChatRequest, ChatResponse, ReceiptAck, SessionCreate, TicketCreate
from app.observability import Metrics
from app.service import SupportService
from app.tools import SupportTools, acknowledge, ticket_from_row

Context = Annotated[AuthContext, Depends(get_context)]


def create_app(settings: Settings | None = None, intelligence=None) -> FastAPI:
    configuration = settings or Settings.from_env()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        app.state.db.initialize()
        yield

    app = FastAPI(title="SupportDesk AI — independent portfolio demo", version="1.0.0", lifespan=lifespan)
    app.state.settings = configuration
    app.state.db = Database(configuration.db_path)
    app.state.intelligence = intelligence or Intelligence(configuration)
    app.state.metrics = Metrics()
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    # httpx informational logs may include administrator-configured endpoint URLs.
    logging.getLogger("httpx").setLevel(logging.WARNING)

    @app.middleware("http")
    async def observe(request: Request, call_next):
        started = time.perf_counter()
        request_id = uuid4().hex
        request.state.request_id = request_id
        status = 500
        try:
            response = await call_next(request)
            status = response.status_code
            response.headers["X-Request-ID"] = request_id
            response.headers["Cache-Control"] = "no-store"
            response.headers["X-Content-Type-Options"] = "nosniff"
            return response
        finally:
            route = getattr(request.scope.get("route"), "path", "unmatched")
            app.state.metrics.request(route, status, (time.perf_counter() - started) * 1000, request_id)

    @app.exception_handler(sqlite3.Error)
    async def database_error(request: Request, exc: sqlite3.Error):
        app.state.metrics.increment("database_errors")
        return JSONResponse(
            status_code=503,
            headers={"Retry-After": "1"},
            content={
                "detail": "The data store is temporarily unavailable. Retry the same submission event.",
                "request_id": request.state.request_id,
            },
        )

    @app.exception_handler(RequestValidationError)
    async def validation_error(request: Request, exc: RequestValidationError):
        # Default validation bodies can echo sensitive input; return locations/types only.
        return JSONResponse(
            status_code=422,
            content={
                "detail": "Invalid request.",
                "errors": [{"location": list(error["loc"]), "type": error["type"]} for error in exc.errors()],
            },
        )

    @app.get("/health")
    def health():
        app.state.db.healthcheck()
        return {"status": "ok", "provider": configuration.llm_provider, "mode": "independent_synthetic_demo"}

    @app.get("/demo/personas")
    def personas():
        return {"personas": PERSONAS}

    @app.post("/demo/sessions")
    def login(body: SessionCreate):
        return issue_session(app.state.db, body.persona, configuration.session_ttl_seconds)

    @app.delete("/demo/sessions")
    def logout(context: Context):
        if context.session_hash:
            with app.state.db.transaction(write=True) as conn:
                conn.execute("DELETE FROM sessions WHERE token_hash = ?", (context.session_hash,))
        return {"status": "signed_out"}

    @app.get("/me/subscription")
    def subscription(context: Context):
        return {"subscription": SupportTools(app.state.db, context).get_my_subscription()}

    @app.get("/me/transactions")
    def transactions(context: Context):
        return {"transactions": SupportTools(app.state.db, context).get_my_transactions()}

    @app.get("/me/tickets")
    def tickets(context: Context):
        return {"tickets": SupportTools(app.state.db, context).get_my_tickets()}

    @app.get("/articles")
    def articles(q: str = "subscription management"):
        return {"articles": KnowledgeBase().search(q[:2000])}

    @app.post("/chat", response_model=ChatResponse)
    def chat(body: ChatRequest, context: Context):
        response = SupportService(app.state.db, app.state.intelligence).handle(body, context)
        app.state.metrics.increment(f"intent:{response.intent.value}")
        app.state.metrics.increment(f"case_status:{response.case_status}")
        if response.debug.fallback_reason:
            app.state.metrics.increment("model_fallbacks")
        if response.ticket:
            app.state.metrics.increment(
                "ticket_duplicate_reused" if response.data.get("deduplicated") else "tickets_created"
            )
        return response

    @app.post("/tickets")
    def create_ticket(body: TicketCreate, context: Context):
        ticket, deduplicated = SupportTools(app.state.db, context).create_support_ticket(
            body.category, body.description, body.transaction_id, event_id=body.event_id
        )
        app.state.metrics.increment("ticket_duplicate_reused" if deduplicated else "tickets_created")
        return {"ticket": ticket, "deduplicated": deduplicated}

    @app.post("/responses/{response_id}/ack")
    def receipt(response_id: str, body: ReceiptAck, context: Context):
        return acknowledge(app.state.db, context, response_id, body.receipt_token)

    @app.get("/review/tickets", dependencies=[Depends(require_reviewer)])
    def review_tickets():
        with app.state.db.transaction() as conn:
            rows = conn.execute("SELECT * FROM tickets ORDER BY created_at DESC LIMIT 100").fetchall()
        return {"tickets": [ticket_from_row(row) for row in rows]}

    @app.get("/review/metrics", dependencies=[Depends(require_reviewer)])
    def review_metrics():
        return app.state.metrics.snapshot()

    return app


app = create_app()
