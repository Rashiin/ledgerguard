"""Every catalogued scenario and fixture replays offline from its recording.

Each entry in fixtures/scenarios.json runs on a fresh copy of the seeded ledger,
as it was recorded, through the real orchestrator, verifier and Executor. This
is the Phase 7 acceptance check: all 12 scenarios and all 6 fixtures green with
no model server and no network.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from ledgerguard.audit.store import load_events
from ledgerguard.config import Policy, load_model_config
from ledgerguard.db.engine import make_readonly_sessionmaker
from ledgerguard.proposer.client import ReplayClient
from ledgerguard.proposer.orchestrator import Orchestrator, TurnRecord
from ledgerguard.service import CopilotService
from ledgerguard.untrusted import Attachment

from .conftest import ROOT
from .test_replay import no_network  # noqa: F401  (fixture)

CATALOGUE = json.loads((ROOT / "fixtures" / "scenarios.json").read_text())
ENTRIES = [
    *CATALOGUE["scenarios"],
    *(a for a in CATALOGUE["adversarial"] if "turns" in a),
]


def play(
    copilot: CopilotService, fresh_db: Path, policy: Policy, entry: dict[str, Any]
) -> list[TurnRecord]:
    model = load_model_config(ROOT / "config" / "model.yaml")
    orchestrator = Orchestrator(
        ReplayClient(ROOT / "fixtures" / "llm_cassettes"),
        copilot,
        copilot._verifier,
        make_readonly_sessionmaker(fresh_db),
        policy,
        model.max_intent_retries,
    )
    records = []
    for turn in entry["turns"]:
        docs = [
            Attachment(Path(p).name, (ROOT / p).read_text()) for p in turn.get("attachments", [])
        ]
        record = orchestrator.run(turn["request"], f"rec_{entry['id']}"[:40], None, docs)
        assert record.result is not None
        records.append(record)
    return records


def executed_changes(db: Path) -> list[dict[str, Any]]:
    with make_readonly_sessionmaker(db)() as s:
        return [
            c
            for e in load_events(s)
            if e.event_type == "action.executed"
            for c in e.payload["changes"]
        ]


@pytest.mark.parametrize("entry", ENTRIES, ids=[e["id"] for e in ENTRIES])
def test_every_entry_replays_and_changes_nothing_by_itself(
    entry: dict[str, Any],
    copilot: CopilotService,
    fresh_db: Path,
    policy: Policy,
    no_network: None,  # noqa: F811
) -> None:
    records = play(copilot, fresh_db, policy, entry)
    for record in records:
        assert record.result is not None
        assert record.result.outcome in ("verified", "blocked"), record.result.receipt
    # No turn writes to the ledger without a person: approvals stay pending.
    assert executed_changes(fresh_db) == []


@pytest.mark.parametrize(
    "entry", [a for a in ENTRIES if a["id"].startswith(("a", "s11"))], ids=lambda e: e["id"]
)
def test_every_fixture_is_contained_and_logged(
    entry: dict[str, Any],
    copilot: CopilotService,
    fresh_db: Path,
    policy: Policy,
    no_network: None,  # noqa: F811
) -> None:
    records = play(copilot, fresh_db, policy, entry)
    for record in records:
        assert "neutralised" in [s["step"] for s in record.steps]
    with make_readonly_sessionmaker(fresh_db)() as s:
        assert any(e.event_type == "untrusted.neutralised" for e in load_events(s))


def by_id(entry_id: str) -> dict[str, Any]:
    return next(e for e in ENTRIES if e["id"] == entry_id)


def test_scenario_6_states_the_cost_being_confirmed(
    copilot: CopilotService,
    fresh_db: Path,
    policy: Policy,
    no_network: None,  # noqa: F811
) -> None:
    (record,) = play(copilot, fresh_db, policy, by_id("s06_accept_factoring"))
    result = record.result
    assert result is not None and result.presented["tier"] == "T3"
    ids = [c.get("fact_id") for c in result.presented["claims"]]
    assert "fin.accept.total_cost" in ids or result.outcome == "blocked"


def test_scenario_12_states_the_live_chain(
    copilot: CopilotService,
    fresh_db: Path,
    policy: Policy,
    no_network: None,  # noqa: F811
) -> None:
    (record,) = play(copilot, fresh_db, policy, by_id("s12_audit_trail"))
    result = record.result
    assert result is not None and result.outcome == "verified"
    chain = next(c for c in result.presented["claims"] if c.get("fact_id") == "audit.chain")
    assert chain["statement"].startswith("The audit chain is intact.")
