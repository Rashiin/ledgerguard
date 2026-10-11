"""FastAPI application.

Every request handler reads through the read-only session. The only writer
is the Executor, built once at startup; this module never opens a writable
connection itself.
"""

from __future__ import annotations

import hashlib
import json
import os
import queue
import threading
from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager
from decimal import Decimal
from pathlib import Path
from typing import Annotated, Any, Literal

from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, ValidationError
from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

import ledgerguard
from ledgerguard.analytics import analysis, financing, radar
from ledgerguard.analytics.cashflow import ProjectedWeek
from ledgerguard.audit.store import event_dict, load_events, verify_stored
from ledgerguard.config import RuleParameters, Settings, load_settings
from ledgerguard.db.engine import make_readonly_sessionmaker
from ledgerguard.db.models import Business, Invoice, Transaction
from ledgerguard.domain.enums import Actor
from ledgerguard.executor.executor import ExecutionRefused, Executor
from ledgerguard.ledger import queries
from ledgerguard.proposer.client import make_client
from ledgerguard.proposer.orchestrator import Orchestrator
from ledgerguard.service import CopilotService
from ledgerguard.untrusted import Attachment
from ledgerguard.verifier import intent as I
from ledgerguard.verifier.pipeline import Verifier
from ledgerguard.verifier.seal import Sealer, load_or_create_key
from ledgerguard.verifier.snapshot import load_snapshot


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


class ScenarioBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # Checked against the same strict model the verifier uses, in JSON mode, so a
    # date arrives as an ISO string exactly as it does from the model.
    scenario: dict[str, Any]


SCENARIO_ADAPTER: TypeAdapter[Any] = TypeAdapter(I.Scenario)


class MatchBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # Each weight is a decimal string, so the API never takes a float.
    weights: dict[str, Annotated[str, Field(pattern=r"^\d{1,3}(\.\d{1,4})?$")]] = Field(
        default_factory=dict
    )


def _week(w: ProjectedWeek) -> dict[str, Any]:
    return {
        "week": w.index,
        "starts_on": w.starts_on.isoformat(),
        "ends_on": w.ends_on.isoformat(),
        "opening": str(w.opening),
        "inflow": {c.value: str(v) for c, v in w.inflow.items()},
        "outflow": {c.value: str(v) for c, v in w.outflow.items()},
        "closing_contracted": str(w.closing_contracted),
        "closing_with_expected": str(w.closing_with_expected),
        "closing": str(w.closing),
    }


class AttachmentBody(BaseModel):
    """Text extracted from a document the owner attached. Treated as outside content."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=120)
    text: str = Field(min_length=1, max_length=20000)


class ChatBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    message: str = Field(min_length=1, max_length=2000)
    attachments: list[AttachmentBody] = Field(default_factory=list, max_length=3)
    conversation_id: str = Field(default="conv_default", pattern=r"^[A-Za-z0-9_-]{1,40}$")


def _sse(event: str, data: Any) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


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
        verifier = Verifier(sealer, settings.policy)
        app.state.copilot = CopilotService(app.state.read_sessions, verifier, executor)
        app.state.orchestrator = Orchestrator(
            make_client(settings.model, settings.config_dir.resolve().parent),
            app.state.copilot,
            verifier,
            app.state.read_sessions,
            settings.policy,
            settings.model.max_intent_retries,
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

    @app.post("/api/chat")
    def chat(body: ChatBody, request: Request) -> StreamingResponse:
        """Run one turn and stream each step, then the verified result."""
        orchestrator: Orchestrator = request.app.state.orchestrator
        events: queue.Queue[tuple[str, Any] | None] = queue.Queue()

        def work() -> None:
            try:
                record = orchestrator.run(
                    body.message,
                    body.conversation_id,
                    lambda s: events.put(("step", s)),
                    [Attachment(a.name, a.text) for a in body.attachments],
                )
                result = record.result
                assert result is not None
                events.put(("turn", {**result.presented, "receipt": result.receipt}))
            except Exception as exc:  # the stream must end with an error event, not hang
                events.put(("error", {"message": f"{type(exc).__name__}: {exc}"}))
            finally:
                events.put(None)

        threading.Thread(target=work, daemon=True).start()

        def stream() -> Iterator[str]:
            while (item := events.get()) is not None:
                yield _sse(*item)

        return StreamingResponse(stream(), media_type="text/event-stream")

    def rules() -> RuleParameters:
        return settings.policy.rules

    @app.get("/api/cashflow/projection")
    def projection(
        session: ReadSession, weeks: Annotated[int, Query(ge=1, le=13)] = 13
    ) -> dict[str, Any]:
        snap = load_snapshot(session)
        return {
            "as_of": snap.as_of.isoformat(),
            "cash_on_hand": str(snap.cash_on_hand()),
            "primary_band": "contracted",
            "weeks": [_week(w) for w in snap.weeks[:weeks]],
        }

    @app.get("/api/alerts")
    def alerts(session: ReadSession) -> dict[str, Any]:
        snap = load_snapshot(session)
        r = rules()
        found = radar.compute_alerts(
            snap,
            radar.baseline(snap),
            r.protection_band,
            r.payroll_margin_warning,
            r.concentration_warning_share,
        )
        return {"as_of": snap.as_of.isoformat(), "alerts": [a.as_dict() for a in found]}

    @app.post("/api/cashflow/scenario")
    def scenario(body: ScenarioBody, session: ReadSession) -> dict[str, Any]:
        try:
            scenario = SCENARIO_ADAPTER.validate_json(json.dumps(body.scenario))
        except ValidationError as problem:
            raise HTTPException(422, problem.errors(include_url=False)) from None
        snap = load_snapshot(session)
        r = rules()
        try:
            result = radar.simulate(
                snap,
                SCENARIO_ADAPTER.dump_python(scenario, mode="json"),
                r.protection_band,
                r.payroll_margin_warning,
                r.concentration_warning_share,
            )
        except ValueError as problem:
            raise HTTPException(422, str(problem)) from None
        return result.as_dict()

    @app.post("/api/financing/match")
    def financing_match(body: MatchBody, session: ReadSession) -> dict[str, Any]:
        snap = load_snapshot(session)
        try:
            weights = financing.normalise({k: Decimal(v) for k, v in body.weights.items()})
        except ValueError as problem:
            raise HTTPException(422, str(problem)) from None
        return financing.match(snap, weights).as_dict()

    @app.get("/api/analysis")
    def analysis_summary(session: ReadSession) -> dict[str, Any]:
        snap = load_snapshot(session)
        return {
            "as_of": snap.as_of.isoformat(),
            "metrics": [
                {"name": m.name, "value": str(m.value), "unit": m.unit, "method": m.method}
                for m in analysis.metrics(snap).values()
            ],
            "anomalies": [
                {
                    "id": a.id,
                    "kind": a.kind,
                    "rows": list(a.rows),
                    "statement": a.statement,
                    "amount": str(a.amount),
                    "booked_on": a.booked_on.isoformat(),
                }
                for a in analysis.anomalies(snap)
            ],
        }

    @app.get("/api/demo/scenarios")
    def demo_scenarios() -> dict[str, Any]:
        """The recorded scenarios and fixtures, with any attachment text, for the UI."""
        root = settings.config_dir.parent
        catalogue: dict[str, Any] = json.loads((root / "fixtures" / "scenarios.json").read_text())
        for entry in [*catalogue["scenarios"], *catalogue["adversarial"]]:
            for turn in entry.get("turns", []):
                turn["attachments"] = [
                    {"name": Path(p).name, "text": (root / p).read_text()}
                    for p in turn.get("attachments", [])
                ]
        return catalogue

    @app.get("/api/receipts/{receipt_id}")
    def receipt(receipt_id: str, service: Copilot) -> dict[str, Any]:
        body = service.receipt(receipt_id)
        if body is None:
            raise HTTPException(404, f"receipt {receipt_id} not found")
        return body

    @app.get("/api/approvals")
    def approvals(service: Copilot) -> dict[str, Any]:
        return {"pending": service.pending()}

    @app.get("/api/approvals/{intent_id}/diff")
    def approval_diff(intent_id: str, service: Copilot) -> dict[str, Any]:
        try:
            changes = service.preview(intent_id)
        except ExecutionRefused as refusal:
            raise HTTPException(409, str(refusal)) from None
        return {"intent_id": intent_id, "changes": changes}

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

    # The built web UI, when present. API routes above take precedence.
    web = settings.web_dir
    if web is not None and (web / "index.html").is_file():
        app.mount("/", StaticFiles(directory=web, html=True), name="web")

    return app


def main() -> None:
    import uvicorn

    uvicorn.run(
        create_app(),
        host=os.environ.get("LEDGERGUARD_HOST", "127.0.0.1"),
        port=int(os.environ.get("LEDGERGUARD_PORT", "8000")),
    )
