"""Recorded turns replay offline through the real orchestrator and verifier.

No model server and no network: the ReplayClient answers from
fixtures/llm_cassettes. A cassette is matched by the fingerprint of the exact
request, so a change to the prompt, the facts or the schema makes these tests
fail until the turn is recorded again.
"""

from __future__ import annotations

import socket
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from ledgerguard.config import Policy, load_model_config
from ledgerguard.proposer.client import ReplayClient, ReplayMiss
from ledgerguard.proposer.orchestrator import Orchestrator, TurnRecord
from ledgerguard.service import CopilotService

from .conftest import ROOT

CASSETTES = ROOT / "fixtures" / "llm_cassettes"


@pytest.fixture
def no_network(monkeypatch: pytest.MonkeyPatch) -> None:
    def refuse(*args: Any, **kwargs: Any) -> None:
        raise AssertionError("a replayed turn tried to open a network connection")

    monkeypatch.setattr(socket, "create_connection", refuse)
    monkeypatch.setattr(socket.socket, "connect", refuse)


@pytest.fixture
def orchestrator(
    copilot: CopilotService, fresh_db: Path, policy: Policy, no_network: None
) -> Iterator[Orchestrator]:
    from ledgerguard.db.engine import make_readonly_sessionmaker

    model = load_model_config(ROOT / "config" / "model.yaml")
    yield Orchestrator(
        ReplayClient(CASSETTES),
        copilot,
        copilot._verifier,
        make_readonly_sessionmaker(fresh_db),
        policy,
        model.max_intent_retries,
    )


def run(orchestrator: Orchestrator, request: str) -> TurnRecord:
    record = orchestrator.run(request, "conv_replay")
    assert record.result is not None
    return record


def test_scenario_1_replays_offline_and_leads_with_the_shortfall(
    orchestrator: Orchestrator,
) -> None:
    record = run(orchestrator, "How is our cash looking for the next quarter?")
    result = record.result
    assert result is not None
    assert (result.outcome, result.status) == ("verified", "executed")
    first = result.presented["claims"][0]
    assert first["fact_id"] == "proj.shortfall" and first["mandatory"] is True
    assert first["statement"].startswith("Projected cash falls below zero in weeks 6 to 8.")
    assert first["commentary"]
    assert result.receipt["risk_flags"] == []
    assert [s["step"] for s in record.steps] == ["plan", "tools", "model", "verified"]


def test_scenario_9_replays_offline_with_one_clean_block(orchestrator: Orchestrator) -> None:
    record = run(orchestrator, "Pay all outstanding supplier invoices this week")
    result = record.result
    assert result is not None
    assert result.outcome == "blocked" and result.presented["tier"] == "T3"
    assert [b["rule"] for b in result.presented["blocked_by"]] == ["PayrollProtection"]
    assert "MATERIAL_OMISSION" not in result.receipt["risk_flags"]
    assert result.presented["claims"][0]["fact_id"] == "block.PayrollProtection"
    assert "check_action" in result.receipt["tools_called"]
    steps = [s["step"] for s in record.steps]
    assert steps == ["plan", "tools", "model", "dry_run", "model", "verified"]


def test_an_unrecorded_request_fails_loudly(orchestrator: Orchestrator) -> None:
    with pytest.raises(ReplayMiss, match="no recorded response"):
        orchestrator.run("Something nobody recorded", "conv_replay")


def test_replay_client_needs_only_the_cassette_directory(no_network: None) -> None:
    client = ReplayClient(CASSETTES)
    assert client._responses  # loaded from disk, nothing else touched
