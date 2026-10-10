from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from ledgerguard.api.app import create_app
from ledgerguard.config import Settings, load_model_config, load_policy

from .conftest import ROOT


@pytest.fixture
def client(fresh_db: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    monkeypatch.setenv("LLM_CLIENT", "replay")
    settings = Settings(
        db_path=fresh_db,
        config_dir=ROOT / "config",
        policy=load_policy(ROOT / "config" / "policy.yaml"),
        model=load_model_config(ROOT / "config" / "model.yaml"),
        key_path=tmp_path / "verifier.key",
    )
    with TestClient(create_app(settings)) as c:
        yield c


def test_health(client: TestClient) -> None:
    response = client.get("/api/health")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["llm_client"] == "replay"
    assert body["ledger"]["data_as_of"] == "2026-10-16"


def test_startup_is_audited_and_chain_verifies(client: TestClient) -> None:
    events = client.get("/api/audit").json()["events"]
    assert [e["event_type"] for e in events] == ["system.started"]
    assert len(events[0]["payload"]["policy_sha256"]) == 64
    assert client.get("/api/audit/verify").json() == {
        "intact": True,
        "events": 1,
        "head_hash": events[0]["hash"],
    }


def test_verify_endpoint_reports_tampering(client: TestClient, fresh_db: Path) -> None:
    import sqlite3

    conn = sqlite3.connect(fresh_db)
    conn.execute("DROP TRIGGER audit_event_no_update")
    conn.execute("UPDATE audit_event SET actor = 'owner' WHERE seq = 1")
    conn.commit()
    conn.close()
    body = client.get("/api/audit/verify").json()
    assert body["intact"] is False
    assert body["first_bad_seq"] == 1


def test_ledger_summary(client: TestClient) -> None:
    body = client.get("/api/ledger/summary").json()
    assert body["business"]["data_as_of"] == "2026-10-16"
    assert body["cash_on_hand"] == "129120.63"
    credit = next(a for a in body["accounts"] if a["type"] == "credit_line")
    assert credit["available"] == "100000.00"
    assert body["open_invoices"]["receivable"]["overdue"]["count"] == 3
    assert body["open_invoices"]["receivable"]["disputed"]["count"] == 1
    assert body["next_payroll"]["id"] == "pay_0025"
    assert all(isinstance(a["balance"], str) for a in body["accounts"])


def test_row_drill_down(client: TestClient) -> None:
    body = client.get("/api/ledger/rows/invoice/inv_0412").json()
    assert body["row"]["counterparty_id"] == "cp_brennan"
    assert body["row"]["gross_amount"] == "46854.00"
    assert client.get("/api/ledger/rows/invoice/inv_9999").status_code == 404
    assert client.get("/api/ledger/rows/sqlite_master/x").status_code == 404


def test_audit_paging(client: TestClient) -> None:
    assert client.get("/api/audit?from_seq=2").json()["events"] == []
    assert client.get("/api/audit?limit=0").status_code == 422


def _submit(client: TestClient, raw: str) -> dict:  # type: ignore[type-arg]
    """Phases 1-3 have no chat endpoint yet; submit through the app's service directly."""
    service = client.app.state.copilot  # type: ignore[attr-defined]
    return service.submit(raw, "conv_api").presented  # type: ignore[no-any-return]


def test_approval_queue_endpoints(client: TestClient) -> None:
    from .conftest import intent_json

    turn = _submit(
        client, intent_json("MARK_DISPUTED", {"invoice_id": "inv_0412", "reason": "damaged"})
    )
    pending = client.get("/api/approvals").json()["pending"]
    assert [p["intent_id"] for p in pending] == [turn["intent_id"]]
    assert pending[0]["tier"] == "T2"
    assert (
        client.post(
            f"/api/approvals/{turn['intent_id']}/approve", json={"approver": "llm"}
        ).status_code
        == 422
    )
    done = client.post(
        f"/api/approvals/{turn['intent_id']}/approve", json={"approver": "operator"}
    ).json()
    assert done["status"] == "executed"
    assert done["changes"][0]["after"] == "disputed"
    again = client.post(
        f"/api/approvals/{turn['intent_id']}/approve", json={"approver": "operator"}
    )
    assert again.status_code == 409 and "at most once" in again.json()["detail"]
    assert client.get("/api/audit/verify").json()["intact"] is True


def test_decline_and_receipt_endpoints(client: TestClient) -> None:
    from .conftest import intent_json

    turn = _submit(client, intent_json("INITIATE_PAYMENT", {"invoice_ids": ["inv_0377"]}))
    receipt = client.get(f"/api/receipts/{turn['receipt_id']}").json()
    assert receipt["tier"] == "T3" and "DUPLICATE_PAYMENT" in receipt["risk_flags"]
    assert client.get("/api/receipts/rcp_missing").status_code == 404
    t3 = client.post(f"/api/approvals/{turn['intent_id']}/approve", json={"approver": "owner"})
    assert t3.status_code == 409 and "confirmation" in t3.json()["detail"]
    declined = client.post(
        f"/api/approvals/{turn['intent_id']}/decline", json={"by": "owner", "reason": "duplicate"}
    ).json()
    assert declined["status"] == "declined"
    assert client.get("/api/approvals").json()["pending"] == []
