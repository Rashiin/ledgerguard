"""Database connections.

This module hands out read-only connections and nothing else. SQLite is
opened with ``mode=ro`` and every connection also sets ``PRAGMA
query_only=ON``, so a write through this engine fails inside SQLite.

Writable connections are built in two places. The Executor makes ledger
changes while the app runs. ``ledgerguard.provisioning`` runs migrations and
the seeder before the app starts.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from sqlalchemy import Engine, create_engine, event
from sqlalchemy.orm import Session, sessionmaker


def readonly_url(db_path: Path) -> str:
    return f"sqlite:///file:{db_path.resolve()}?mode=ro&uri=true"


def make_readonly_engine(db_path: Path) -> Engine:
    if not db_path.exists():
        raise FileNotFoundError(f"ledger database not found at {db_path}; run scripts/seed.py")
    engine = create_engine(readonly_url(db_path))

    @event.listens_for(engine, "connect")
    def _query_only(dbapi_connection: Any, _record: Any) -> None:
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA query_only=ON")
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    return engine


def make_readonly_sessionmaker(db_path: Path) -> sessionmaker[Session]:
    return sessionmaker(bind=make_readonly_engine(db_path), autoflush=False)
