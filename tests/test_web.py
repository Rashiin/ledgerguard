"""The API serves the built web UI and the approval diff."""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from ledgerguard.api.app import create_app
from ledgerguard.config import Settings, load_model_config, load_policy

from .conftest import intent_json

ROOT = Path(__file__).resolve().parents[1]


def _settings(db: Path, tmp: Path, web: Path | None) -> Settings:
    return Settings(
        db_path=db,
        config_dir=ROOT / "config",
        policy=load_policy(ROOT / "config" / "policy.yaml"),
        model=load_model_config(ROOT / "config" / "model.yaml"),
        key_path=tmp / "k.key",
        web_dir=web,
    )


def test_the_built_ui_is_served_and_the_api_still_wins(fresh_db: Path, tmp_path: Path) -> None:
    web = tmp_path / "dist"
    web.mkdir()
    (web / "index.html").write_text("<!doctype html><title>LedgerGuard</title>")
    with TestClient(create_app(_settings(fresh_db, tmp_path, web))) as c:
        assert "LedgerGuard" in c.get("/").text
        assert c.get("/api/health").json()["status"] == "ok"


def test_without_a_build_only_the_api_is_served(fresh_db: Path, tmp_path: Path) -> None:
    with TestClient(create_app(_settings(fresh_db, tmp_path, tmp_path / "missing"))) as c:
        assert c.get("/").status_code == 404
        assert c.get("/api/health").status_code == 200


def test_the_diff_endpoint_previews_without_writing(fresh_db: Path, tmp_path: Path) -> None:
    with TestClient(create_app(_settings(fresh_db, tmp_path, None))) as c:
        turn = c.app.state.copilot.submit(  # type: ignore[attr-defined]
            intent_json("RECORD_PAYMENT", {"invoice_id": "inv_0412"}), "conv"
        )
        diff = c.get(f"/api/approvals/{turn.intent_id}/diff").json()["changes"]
        assert {(d["table"], d["field"]) for d in diff} >= {("invoice", "status")}
        assert c.get("/api/ledger/rows/invoice/inv_0412").json()["row"]["status"] == "overdue"
        assert c.get("/api/approvals/int_nope/diff").status_code == 409
