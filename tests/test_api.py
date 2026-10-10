from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from ledgerguard.api.app import create_app
from ledgerguard.config import Settings, load_model_config, load_policy

from .conftest import ROOT


def test_health(seeded_db: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LLM_CLIENT", "replay")
    settings = Settings(
        db_path=seeded_db,
        config_dir=ROOT / "config",
        policy=load_policy(ROOT / "config" / "policy.yaml"),
        model=load_model_config(ROOT / "config" / "model.yaml"),
    )
    response = TestClient(create_app(settings)).get("/api/health")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["llm_client"] == "replay"
    assert body["ledger"]["data_as_of"] == "2026-10-16"
