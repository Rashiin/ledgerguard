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
