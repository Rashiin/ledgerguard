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
from ledgerguard.verifier.policy import FINANCING_DISCLOSURE

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
    """The recording made after the adversarial fixtures were seeded. The model chose two
    invoices due on 29 November, and TaxReserveFloor blocked them. Earlier recordings
    chose other invoices and PayrollProtection blocked. See the security self-assessment."""
    record = run(orchestrator, "Pay all outstanding supplier invoices this week")
    result = record.result
    assert result is not None
    assert result.outcome == "blocked" and result.presented["tier"] == "T3"
    assert [b["rule"] for b in result.presented["blocked_by"]] == ["TaxReserveFloor"]
    assert "MATERIAL_OMISSION" not in result.receipt["risk_flags"]
    assert result.presented["claims"][0]["fact_id"] == "block.TaxReserveFloor"
    assert "check_action" in result.receipt["tools_called"]
    steps = [s["step"] for s in record.steps]
    assert steps == ["plan", "neutralised", "tools", "model", "dry_run", "model", "verified"]


def test_scenario_2_drills_week_7_down_to_its_rows(orchestrator: Orchestrator) -> None:
    result = run(orchestrator, "Why is week 7 so bad?").result
    assert result is not None and result.outcome == "verified"
    rows = {r for c in result.presented["claims"] for r in c["sources"]}
    assert {"inv_0665", "inv_0681", "inv_0666"} <= rows
    figures = {c["figure"] for c in result.presented["claims"]}
    assert {"142500.00", "33953.66", "33904.50"} <= figures


def test_scenario_3_runs_the_late_payment_and_reports_its_result(
    orchestrator: Orchestrator,
) -> None:
    record = run(orchestrator, "What if Brennan Hospitality pays 30 days late?")
    result = record.result
    assert result is not None
    assert (result.outcome, result.presented["tier"]) == ("verified", "T1")
    assert [s["step"] for s in record.steps] == [
        "plan",
        "tools",
        "model",
        "scenario",
        "model",
        "verified",
    ]
    scenario = next(c for c in result.presented["claims"] if c.get("fact_id") == "scn.result")
    assert scenario["statement"].startswith("With Brennan Hospitality Group paying 30 days later")
    assert scenario["figure"] == "-424047.56"
    assert "run_scenario" in result.receipt["tools_called"]


def test_scenario_4_lists_options_with_costs_and_the_do_nothing_line(
    orchestrator: Orchestrator,
) -> None:
    result = run(orchestrator, "We're short in week 7 — what are our options?").result
    assert result is not None
    assert (result.outcome, result.presented["tier"]) == ("verified", "T1")
    ids = [c.get("fact_id") for c in result.presented["claims"]]
    assert {"proj.shortfall", "fin.ranking", "fin.do_nothing"} <= set(ids)
    assert result.presented["disclosures"] == [FINANCING_DISCLOSURE]


def test_scenario_5_compares_total_cost_in_euros(orchestrator: Orchestrator) -> None:
    result = run(
        orchestrator,
        "Compare the factoring option against the credit line over the real drawdown period",
    ).result
    assert result is not None and result.outcome == "verified"
    text = " ".join(c["statement"] for c in result.presented["claims"])
    assert "€512.05" in text and "€4,164.35" in text
    assert "TEXT_FIGURE_MASKED" not in result.receipt["risk_flags"]


def test_scenario_10_flags_the_planted_anomalies(orchestrator: Orchestrator) -> None:
    result = run(orchestrator, "Anything unusual in last month's transactions?").result
    assert result is not None
    assert (result.outcome, result.presented["tier"]) == ("verified", "T0")
    rows = {r for c in result.presented["claims"] for r in c["sources"]}
    assert "txn_0869" in rows and {"inv_0641", "inv_0377"} <= rows


def test_an_unrecorded_request_fails_loudly(orchestrator: Orchestrator) -> None:
    with pytest.raises(ReplayMiss, match="no recorded response"):
        orchestrator.run("Something nobody recorded", "conv_replay")


def test_replay_client_needs_only_the_cassette_directory(no_network: None) -> None:
    client = ReplayClient(CASSETTES)
    assert client._responses  # loaded from disk, nothing else touched
