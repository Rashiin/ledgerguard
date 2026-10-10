"""FastAPI application.

The API is wired to the read-only session only. It has no way to write to
the ledger; mutations will go through the Executor (Phase 2).
"""

from __future__ import annotations

import os
from typing import Any

from fastapi import FastAPI, Request
from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

import ledgerguard
from ledgerguard.config import Settings, load_settings
from ledgerguard.db.engine import make_readonly_sessionmaker
from ledgerguard.db.models import Business, Invoice, Transaction


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or load_settings()
    app = FastAPI(title="LedgerGuard", version=ledgerguard.__version__)
    app.state.settings = settings
    app.state.read_sessions = make_readonly_sessionmaker(settings.db_path)

    @app.get("/api/health")
    def health(request: Request) -> dict[str, Any]:
        sessions: sessionmaker[Session] = request.app.state.read_sessions
        with sessions() as session:
            business = session.scalars(select(Business)).one()
            invoices = session.scalar(select(func.count()).select_from(Invoice))
            transactions = session.scalar(select(func.count()).select_from(Transaction))
        return {
            "status": "ok",
            "version": ledgerguard.__version__,
            "llm_client": settings.model.client.value,
            "ledger": {
                "business": business.legal_name,
                "data_as_of": business.data_as_of.isoformat(),
                "invoices": invoices,
                "transactions": transactions,
            },
        }

    return app


def main() -> None:
    import uvicorn

    uvicorn.run(
        create_app(),
        host=os.environ.get("LEDGERGUARD_HOST", "127.0.0.1"),
        port=int(os.environ.get("LEDGERGUARD_PORT", "8000")),
    )
