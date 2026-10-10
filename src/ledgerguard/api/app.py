"""FastAPI application.

Every request handler reads through the read-only session. The only writer
is the Executor, built once at startup; this module never opens a writable
connection itself.
"""

from __future__ import annotations

import hashlib
import os
from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager
from typing import Annotated, Any, Literal

from fastapi import Depends, FastAPI, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict
from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

import ledgerguard
from ledgerguard.audit.store import event_dict, load_events, verify_stored
from ledgerguard.config import Settings, load_settings
from ledgerguard.db.engine import make_readonly_sessionmaker
from ledgerguard.db.models import Business, Invoice, Transaction
from ledgerguard.domain.enums import Actor
from ledgerguard.executor.executor import ExecutionRefused, Executor
from ledgerguard.ledger import queries
from ledgerguard.service import CopilotService
from ledgerguard.verifier.pipeline import Verifier
from ledgerguard.verifier.seal import Sealer, load_or_create_key


def read_session(request: Request) -> Iterator[Session]:
    sessions: sessionmaker[Session] = request.app.state.read_sessions
    with sessions() as session:
        yield session


ReadSession = Annotated[Session, Depends(read_session)]


def copilot(request: Request) -> CopilotService:
    service: CopilotService = request.app.state.copilot
    return service


Copilot = Annotated[CopilotService, Depends(copilot)]


class ApproveBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    approver: Literal["operator", "owner"]
    confirmation: str | None = None  # required for T3: the phrase on the receipt


class DeclineBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    by: Literal["operator", "owner"]
    reason: str


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or load_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        sealer = Sealer(load_or_create_key(settings.key_path))
        executor = Executor(settings.db_path, sealer, settings.policy)
        app.state.sealer = sealer
        app.state.executor = executor
        app.state.copilot = CopilotService(
            app.state.read_sessions, Verifier(sealer, settings.policy), executor
        )
        policy_file = settings.config_dir / "policy.yaml"
        executor.record(
            Actor.SYSTEM,
            "system.started",
            {
                "version": ledgerguard.__version__,
                "llm_client": settings.model.client.value,
                "policy_sha256": hashlib.sha256(policy_file.read_bytes()).hexdigest(),
            },
        )
        yield
        executor.dispose()

    app = FastAPI(title="LedgerGuard", version=ledgerguard.__version__, lifespan=lifespan)
    app.state.settings = settings
    app.state.read_sessions = make_readonly_sessionmaker(settings.db_path)

    @app.get("/api/health")
    def health(session: ReadSession) -> dict[str, Any]:
        business = session.scalars(select(Business)).one()
        return {
            "status": "ok",
            "version": ledgerguard.__version__,
            "llm_client": settings.model.client.value,
            "ledger": {
                "business": business.legal_name,
                "data_as_of": business.data_as_of.isoformat(),
                "invoices": session.scalar(select(func.count()).select_from(Invoice)),
                "transactions": session.scalar(select(func.count()).select_from(Transaction)),
            },
        }

    @app.get("/api/ledger/summary")
    def ledger_summary(session: ReadSession) -> dict[str, Any]:
        return queries.summary(session)

    @app.get("/api/ledger/rows/{table}/{row_id}")
    def ledger_row(table: str, row_id: str, session: ReadSession) -> dict[str, Any]:
        """One source row, for drilling down from any figure to what it was computed from."""
        try:
            row = queries.get_row(session, table, row_id)
        except KeyError:
            raise HTTPException(404, f"unknown table {table!r}") from None
        if row is None:
            raise HTTPException(404, f"{table} {row_id} not found")
        return {"table": table, "row": row}

    @app.get("/api/audit")
    def audit(
        session: ReadSession,
        from_seq: Annotated[int, Query(ge=1)] = 1,
        limit: Annotated[int, Query(ge=1, le=1000)] = 100,
    ) -> dict[str, Any]:
        events = load_events(session, from_seq, limit)
        return {"events": [event_dict(ev) for ev in events]}

    @app.get("/api/audit/verify")
    def audit_verify(session: ReadSession) -> dict[str, Any]:
        return verify_stored(session).as_dict()

    @app.get("/api/receipts/{receipt_id}")
    def receipt(receipt_id: str, service: Copilot) -> dict[str, Any]:
        body = service.receipt(receipt_id)
        if body is None:
            raise HTTPException(404, f"receipt {receipt_id} not found")
        return body

    @app.get("/api/approvals")
    def approvals(service: Copilot) -> dict[str, Any]:
        return {"pending": service.pending()}

    @app.post("/api/approvals/{intent_id}/approve")
    def approve(intent_id: str, body: ApproveBody, service: Copilot) -> dict[str, Any]:
        try:
            result = service.approve(intent_id, Actor(body.approver), body.confirmation)
        except ExecutionRefused as refusal:
            raise HTTPException(409, str(refusal)) from None
        return {
            "intent_id": result.intent_id,
            "status": "executed",
            "changes": result.changes,
            "audit_seq": result.audit_seq,
        }

    @app.post("/api/approvals/{intent_id}/decline")
    def decline(intent_id: str, body: DeclineBody, service: Copilot) -> dict[str, Any]:
        try:
            seq = service.decline(intent_id, Actor(body.by), body.reason)
        except ExecutionRefused as refusal:
            raise HTTPException(409, str(refusal)) from None
        return {"intent_id": intent_id, "status": "declined", "audit_seq": seq}

    return app


def main() -> None:
    import uvicorn

    uvicorn.run(
        create_app(),
        host=os.environ.get("LEDGERGUARD_HOST", "127.0.0.1"),
        port=int(os.environ.get("LEDGERGUARD_PORT", "8000")),
    )
