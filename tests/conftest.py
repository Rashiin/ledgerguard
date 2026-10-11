from __future__ import annotations

from pathlib import Path

import pytest

from ledgerguard.provisioning import create_fresh
from ledgerguard.seed import nordvik
from ledgerguard.seed.nordvik import SeedResult

ROOT = Path(__file__).resolve().parent.parent
CATALOGUE = ROOT / "fixtures" / "financing_products.json"


@pytest.fixture(scope="session")
def seed_result() -> SeedResult:
    return nordvik.build(42, CATALOGUE)


@pytest.fixture(scope="session")
def seeded_db(tmp_path_factory: pytest.TempPathFactory) -> Path:
    path = tmp_path_factory.mktemp("ledger") / "ledger.sqlite"
    create_fresh(path, nordvik.build(42, CATALOGUE).rows)
    return path


@pytest.fixture
def fresh_db(tmp_path: Path, seeded_db: Path) -> Path:
    """A private copy of the seeded ledger, for tests that write."""
    import shutil

    path = tmp_path / "ledger.sqlite"
    shutil.copyfile(seeded_db, path)
    return path


@pytest.fixture(scope="session")
def policy():  # type: ignore[no-untyped-def]
    from ledgerguard.config import load_policy

    return load_policy(ROOT / "config" / "policy.yaml")


# --------------------------------------------------------------------------- verifier helpers

import json as _json  # noqa: E402
from collections.abc import Iterator  # noqa: E402
from datetime import UTC, datetime, timedelta  # noqa: E402
from typing import Any  # noqa: E402

T0 = datetime(2026, 10, 16, 9, 0, tzinfo=UTC)
TEST_KEY = bytes(range(32))


class Clock:
    """A controllable clock shared by the verifier, Executor and service."""

    def __init__(self) -> None:
        self.now = T0

    def __call__(self) -> datetime:
        return self.now

    def advance(self, seconds: int) -> None:
        self.now += timedelta(seconds=seconds)


def intent_json(
    kind: str,
    payload: dict[str, Any],
    claims: list[dict[str, Any]] | None = None,
    rationale: str = "Stub proposer output.",
    **extra: Any,
) -> str:
    return _json.dumps(
        {"kind": kind, "payload": payload, "claims": claims or [], "rationale": rationale, **extra}
    )


@pytest.fixture
def snapshot(seeded_db: Path):  # type: ignore[no-untyped-def]
    """A fresh, private in-memory snapshot; tests may mutate it freely."""
    from ledgerguard.db.engine import make_readonly_sessionmaker
    from ledgerguard.verifier.snapshot import load_snapshot

    with make_readonly_sessionmaker(seeded_db)() as session:
        return load_snapshot(session)


@pytest.fixture
def clock() -> Clock:
    return Clock()


@pytest.fixture
def copilot(fresh_db: Path, policy, clock: Clock) -> Iterator[Any]:  # type: ignore[no-untyped-def]
    from ledgerguard.db.engine import make_readonly_sessionmaker
    from ledgerguard.executor.executor import Executor
    from ledgerguard.service import CopilotService
    from ledgerguard.verifier.pipeline import Verifier
    from ledgerguard.verifier.seal import Sealer

    sealer = Sealer(TEST_KEY)
    executor = Executor(fresh_db, sealer, policy, clock=clock)
    ids = iter(f"{n:016x}" for n in range(1, 10_000))
    service = CopilotService(
        make_readonly_sessionmaker(fresh_db),
        Verifier(sealer, policy, clock=clock, new_id=lambda: next(ids)),
        executor,
        clock=clock,
    )
    yield service
    executor.dispose()


def keyed(scenario: dict[str, Any]) -> dict[str, Any]:
    """A flat scenario, as the radar takes it, in the keyed form the model writes."""
    args = {k: v for k, v in scenario.items() if k != "type"}
    return {scenario["type"]: args}
