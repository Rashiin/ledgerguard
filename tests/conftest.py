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
