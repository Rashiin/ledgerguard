"""Offline provisioning: create the ledger file, run migrations, load seed rows.

This is one of the two places allowed to open a writable connection (the
other is the Executor). It runs before the application starts and is never
imported by the API, the proposer or the verifier.
"""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from ledgerguard.db.models import Base

MIGRATIONS_DIR = Path(__file__).parent / "migrations"


def alembic_config(db_path: Path) -> Config:
    cfg = Config()
    cfg.set_main_option("script_location", str(MIGRATIONS_DIR))
    cfg.set_main_option("sqlalchemy.url", f"sqlite:///{db_path.resolve()}")
    return cfg


def migrate(db_path: Path) -> None:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    command.upgrade(alembic_config(db_path), "head")


def _writable_engine(db_path: Path) -> Engine:
    engine = create_engine(f"sqlite:///{db_path.resolve()}")

    @event.listens_for(engine, "connect")
    def _fk_on(dbapi_connection: object, _record: object) -> None:
        cursor = dbapi_connection.cursor()  # type: ignore[attr-defined]
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    return engine


def create_fresh(db_path: Path, rows: Iterable[Base]) -> None:
    """Replace ``db_path`` with a migrated database holding exactly ``rows``.

    Rows are inserted in the order given, in one transaction, so the
    resulting file is identical across runs for identical input.
    """
    for suffix in ("", "-journal", "-wal", "-shm"):
        Path(f"{db_path}{suffix}").unlink(missing_ok=True)
    migrate(db_path)
    engine = _writable_engine(db_path)
    with Session(engine) as session, session.begin():
        for row in rows:
            session.add(row)
            # Flush one row at a time: insert order then matches input order exactly.
            session.flush()
    with engine.connect() as conn:
        conn.exec_driver_sql("VACUUM")
    engine.dispose()
