"""The hash-chained audit log: append, verify, and detect tampering."""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from itertools import pairwise
from pathlib import Path

import pytest
from sqlalchemy.exc import IntegrityError, OperationalError

from ledgerguard.audit.chain import GENESIS, StoredEvent, chain_hash, event_content, verify
from ledgerguard.audit.store import load_events, verify_stored
from ledgerguard.config import Policy
from ledgerguard.db.engine import make_readonly_sessionmaker
from ledgerguard.domain.enums import Actor
from ledgerguard.executor.executor import Executor
from ledgerguard.verifier.seal import Sealer

KEY = bytes(range(32))


def _executor(db: Path, policy: Policy) -> Executor:
    ticks = iter(
        datetime(2026, 10, 16, 9, 0, tzinfo=UTC) + timedelta(seconds=i) for i in range(10_000)
    )
    return Executor(db, Sealer(KEY), policy, clock=lambda: next(ticks))


def _report(db: Path):  # type: ignore[no-untyped-def]
    with make_readonly_sessionmaker(db)() as session:
        return verify_stored(session)


@pytest.fixture
def chained_db(fresh_db: Path, policy: Policy) -> Path:
    ex = _executor(fresh_db, policy)
    ex.record(Actor.SYSTEM, "system.started", {"version": "test"})
    ex.record(Actor.LLM, "intent.proposed", {"kind": "ANSWER_QUERY", "figure": Decimal("1.10")})
    ex.record(Actor.OPERATOR, "approval.granted", {"intent_id": "int_1", "note": "ok"})
    ex.record(Actor.SYSTEM, "action.executed", {"intent_id": "int_1", "changes": []})
    ex.dispose()
    return fresh_db


def test_empty_chain_is_intact(fresh_db: Path) -> None:
    report = _report(fresh_db)
    assert report.intact and report.events == 0 and report.head_hash == GENESIS


def test_appended_chain_is_intact_and_linked(chained_db: Path) -> None:
    report = _report(chained_db)
    assert report.intact
    assert report.events == 4
    with make_readonly_sessionmaker(chained_db)() as session:
        events = load_events(session)
    assert [e.seq for e in events] == [1, 2, 3, 4]
    assert events[0].prev_hash == GENESIS
    for prev, cur in pairwise(events):
        assert cur.prev_hash == prev.hash
    assert events[1].payload["figure"] == "1.10"  # Decimal stored as exact text
    assert report.head_hash == events[-1].hash


def _tamper(db: Path, sql: str) -> None:
    """What an attacker with direct file access could do: drop the guard, then edit."""
    conn = sqlite3.connect(db)
    conn.execute("DROP TRIGGER audit_event_no_update")
    conn.execute("DROP TRIGGER audit_event_no_delete")
    conn.execute(sql)
    conn.commit()
    conn.close()


@pytest.mark.parametrize(
    ("sql", "bad_seq"),
    [
        ('UPDATE audit_event SET payload = \'{"note":"ok","intent_id":"int_2"}\' WHERE seq = 3', 3),
        ("UPDATE audit_event SET actor = 'owner' WHERE seq = 2", 2),
        (
            "UPDATE audit_event SET occurred_at = '2026-10-16T08:00:00.000000+00:00' WHERE seq = 1",
            1,
        ),
        ("DELETE FROM audit_event WHERE seq = 2", 3),
        ("UPDATE audit_event SET hash = 'f' || substr(hash, 2) WHERE seq = 4", 4),
    ],
)
def test_tampering_breaks_the_chain(chained_db: Path, sql: str, bad_seq: int) -> None:
    assert _report(chained_db).intact
    _tamper(chained_db, sql)
    report = _report(chained_db)
    assert not report.intact
    assert report.first_bad_seq == bad_seq
    assert report.reason


def test_rewriting_one_event_and_its_hash_still_breaks_the_next(chained_db: Path) -> None:
    with make_readonly_sessionmaker(chained_db)() as session:
        events = load_events(session)
    ev = events[1]
    forged = event_content(
        id=ev.id,
        seq=ev.seq,
        occurred_at=ev.occurred_at,
        actor="owner",
        event_type=ev.event_type,
        payload=ev.payload,
    )
    new_hash = chain_hash(ev.prev_hash, forged)
    _tamper(chained_db, f"UPDATE audit_event SET actor='owner', hash='{new_hash}' WHERE seq=2")
    report = _report(chained_db)
    assert not report.intact and report.first_bad_seq == 3


def test_database_refuses_update_and_delete(chained_db: Path) -> None:
    conn = sqlite3.connect(chained_db)
    with pytest.raises(sqlite3.IntegrityError, match="append-only"):
        conn.execute("UPDATE audit_event SET event_type = 'x' WHERE seq = 1")
    with pytest.raises(sqlite3.IntegrityError, match="append-only"):
        conn.execute("DELETE FROM audit_event")
    conn.close()
    assert _report(chained_db).intact


def test_float_payload_is_refused(fresh_db: Path, policy: Policy) -> None:
    ex = _executor(fresh_db, policy)
    with pytest.raises((TypeError, IntegrityError, OperationalError)):
        ex.record(Actor.SYSTEM, "bad", {"amount": 1.5})
    ex.dispose()
    assert _report(fresh_db).events == 0


def test_verify_is_pure_over_stored_events() -> None:
    t = datetime(2026, 10, 16, tzinfo=UTC)
    content = event_content(
        id="evt_1", seq=1, occurred_at=t, actor="system", event_type="x", payload={}
    )
    good = StoredEvent("evt_1", 1, t, "system", "x", {}, GENESIS, chain_hash(GENESIS, content))
    assert verify([good]).intact
    out_of_order = StoredEvent("evt_2", 2, t, "system", "x", {}, GENESIS, "0" * 64)
    assert not verify([out_of_order]).intact
