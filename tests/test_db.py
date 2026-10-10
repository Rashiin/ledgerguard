from decimal import Decimal
from pathlib import Path

import pytest
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy import create_engine, text
from sqlalchemy.exc import OperationalError

from ledgerguard.db.engine import make_readonly_engine
from ledgerguard.db.models import Base
from ledgerguard.db.types import canonical_json
from ledgerguard.money import MoneyError


def test_readonly_engine_cannot_write(seeded_db: Path) -> None:
    engine = make_readonly_engine(seeded_db)
    with engine.connect() as conn, pytest.raises(OperationalError):
        conn.execute(text("UPDATE account SET opening_balance = 0"))
    with engine.connect() as conn, pytest.raises(OperationalError):
        conn.execute(text("PRAGMA query_only=OFF"))
        conn.execute(text("DELETE FROM audit_event"))


def test_migration_matches_models(seeded_db: Path) -> None:
    engine = create_engine(f"sqlite:///file:{seeded_db}?mode=ro&uri=true")
    with engine.connect() as conn:
        diff = compare_metadata(MigrationContext.configure(conn), Base.metadata)
    assert diff == []


def test_money_type_refuses_float() -> None:
    from ledgerguard.db.types import Money

    with pytest.raises(MoneyError):
        Money().process_bind_param(1.5, None)  # type: ignore[arg-type]
    assert Money().process_bind_param(Decimal("1.50"), None) == 150  # type: ignore[arg-type]


def test_canonical_json_is_sorted_and_refuses_float() -> None:
    assert canonical_json({"b": Decimal("1.10"), "a": [1, "x"]}) == '{"a":[1,"x"],"b":"1.10"}'
    with pytest.raises(TypeError):
        canonical_json({"rate": 0.1})
