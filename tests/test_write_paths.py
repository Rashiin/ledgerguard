"""Structural invariants: who can write to the database, and what the app imports.

BUILD_SPEC 9.3: at runtime only the Executor writes. The offline provisioning
step (migrations + seeder) is the one documented exception, and the running
application must never import it.
"""

from __future__ import annotations

import ast
import json
import subprocess
import sys
from pathlib import Path

import pytest
from sqlalchemy import update
from sqlalchemy.exc import OperationalError

from ledgerguard.db.engine import make_readonly_sessionmaker
from ledgerguard.db.models import AuditEvent, Invoice
from ledgerguard.domain.enums import InvoiceStatus

from .conftest import ROOT

SRC = ROOT / "src" / "ledgerguard"

# Every place a SQLAlchemy engine is created, and why.
ENGINE_FACTORIES = {
    "db/engine.py": "read-only engine (mode=ro, query_only)",
    "executor/executor.py": "the runtime writer",
    "provisioning.py": "offline migrate + seed",
    "migrations/env.py": "offline migrations, driven by provisioning",
}


def _calls(path: Path, name: str) -> bool:
    tree = ast.parse(path.read_text(), filename=str(path))
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            fn = node.func
            if (isinstance(fn, ast.Name) and fn.id == name) or (
                isinstance(fn, ast.Attribute) and fn.attr == name
            ):
                return True
    return False


def _imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(), filename=str(path))
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            found.add(node.module)
    return found


def test_engines_are_created_only_in_known_places() -> None:
    creators = {str(p.relative_to(SRC)) for p in SRC.rglob("*.py") if _calls(p, "create_engine")}
    assert creators == set(ENGINE_FACTORIES)


def test_no_module_opens_sqlite_directly() -> None:
    offenders = [str(p.relative_to(SRC)) for p in SRC.rglob("*.py") if "sqlite3" in _imports(p)]
    assert offenders == []


def test_only_scripts_import_provisioning_or_the_seeder() -> None:
    offenders = []
    for p in SRC.rglob("*.py"):
        rel = str(p.relative_to(SRC))
        if rel == "provisioning.py" or rel.startswith(("seed/", "migrations/")):
            continue
        if _imports(p) & {
            "ledgerguard.provisioning",
            "ledgerguard.seed",
            "ledgerguard.seed.nordvik",
            "alembic",
            "alembic.command",
        }:
            offenders.append(rel)
    assert offenders == []


def test_running_app_never_loads_provisioning(seeded_db: Path, tmp_path: Path) -> None:
    """Build and start the real app in a clean interpreter, then inspect sys.modules."""
    probe = f"""
import json, os, sys
os.environ["LEDGERGUARD_DB"] = {str(seeded_db)!r}
os.environ["LEDGERGUARD_CONFIG_DIR"] = {str(ROOT / "config")!r}
os.environ["LEDGERGUARD_KEY"] = {str(tmp_path / "k.key")!r}
os.environ["LLM_CLIENT"] = "replay"
from fastapi.testclient import TestClient
from ledgerguard.api.app import create_app
with TestClient(create_app()) as client:
    for path in ("/api/health", "/api/ledger/summary", "/api/audit/verify"):
        assert client.get(path).status_code == 200, path
loaded = sorted(m for m in sys.modules
                if m.startswith(("ledgerguard.provisioning", "ledgerguard.seed", "alembic")))
print(json.dumps(loaded))
"""
    import shutil

    db_copy = tmp_path / "ledger.sqlite"
    shutil.copyfile(seeded_db, db_copy)
    probe = probe.replace(str(seeded_db), str(db_copy))
    out = subprocess.run(
        [sys.executable, "-c", probe], capture_output=True, text=True, check=True, cwd=ROOT
    )
    assert json.loads(out.stdout.strip().splitlines()[-1]) == []


def test_read_session_cannot_write_through_the_orm(fresh_db: Path) -> None:
    """A non-Executor module only ever has this session; writes fail inside SQLite."""
    with make_readonly_sessionmaker(fresh_db)() as session:
        invoice = session.get(Invoice, "inv_0412")
        assert invoice is not None
        invoice.status = InvoiceStatus.PAID
        with pytest.raises(OperationalError, match=r"readonly|read-only|query_only"):
            session.commit()
    with make_readonly_sessionmaker(fresh_db)() as session:
        with pytest.raises(OperationalError):
            session.execute(update(AuditEvent).values(event_type="x"))
            session.commit()
        status = session.get(Invoice, "inv_0412")
        assert status is not None and status.status is InvoiceStatus.OVERDUE
