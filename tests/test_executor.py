"""Executor invariants: only sealed VerifiedActions, tier gates, BLOCK is absolute."""

from __future__ import annotations

import dataclasses
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from ledgerguard.audit.store import load_events, verify_stored
from ledgerguard.config import Policy
from ledgerguard.db.engine import make_readonly_sessionmaker
from ledgerguard.db.models import Invoice
from ledgerguard.domain.enums import ActionKind, Actor, InvoiceStatus, Tier
from ledgerguard.executor.executor import (
    Approval,
    ExecutionRefused,
    Executor,
    t3_confirmation_phrase,
)
from ledgerguard.verifier.seal import Sealer, VerifiedAction, load_or_create_key

KEY = bytes(range(32))
T0 = datetime(2026, 10, 16, 9, 0, tzinfo=UTC)


class Clock:
    def __init__(self) -> None:
        self.now = T0

    def __call__(self) -> datetime:
        return self.now


@pytest.fixture
def clock() -> Clock:
    return Clock()


@pytest.fixture
def executor(fresh_db: Path, policy: Policy, clock: Clock) -> Iterator[Executor]:
    ex = Executor(fresh_db, Sealer(KEY), policy, clock=clock)
    yield ex
    ex.dispose()


def action(
    kind: ActionKind,
    tier: Tier,
    payload: dict[str, Any] | None = None,
    blocking: tuple[str, ...] = (),
    key: bytes = KEY,
) -> VerifiedAction:
    """What the verifier (Phase 3) will hand over; here sealed by a stub."""
    unsealed = VerifiedAction(
        intent_id="int_test",
        kind=kind,
        tier=tier,
        payload=payload or {},
        receipt_id="rcp_test",
        verifier_version="stub",
        verified_at=T0,
        blocking_rules=blocking,
    )
    return Sealer(key).seal(unsealed)


OPERATOR = Approval(Actor.OPERATOR, T0)


def refusals(db: Path) -> list[dict[str, Any]]:
    with make_readonly_sessionmaker(db)() as s:
        return [dict(e.payload) for e in load_events(s) if e.event_type == "action.refused"]


def invoice_status(db: Path, invoice_id: str) -> InvoiceStatus:
    with make_readonly_sessionmaker(db)() as s:
        inv = s.get(Invoice, invoice_id)
        assert inv is not None
        return inv.status


# ---- only sealed VerifiedActions


@pytest.mark.parametrize(
    "not_an_action",
    [
        {"kind": "MARK_DISPUTED", "tier": "T0", "payload": {"invoice_id": "inv_0412"}},
        '{"kind": "MARK_DISPUTED"}',
        None,
        object(),
    ],
)
def test_rejects_anything_that_is_not_a_verified_action(
    executor: Executor, fresh_db: Path, not_an_action: object
) -> None:
    with pytest.raises(ExecutionRefused, match="only a VerifiedAction"):
        executor.execute(not_an_action, OPERATOR)
    assert invoice_status(fresh_db, "inv_0412") is InvoiceStatus.OVERDUE
    assert refusals(fresh_db)[-1]["received_type"] == type(not_an_action).__name__


def test_rejects_an_unsealed_action(executor: Executor, fresh_db: Path) -> None:
    unsealed = dataclasses.replace(
        action(ActionKind.MARK_DISPUTED, Tier.T2, {"invoice_id": "inv_0412"}), seal=""
    )
    with pytest.raises(ExecutionRefused, match="seal"):
        executor.execute(unsealed, OPERATOR)
    assert invoice_status(fresh_db, "inv_0412") is InvoiceStatus.OVERDUE


def test_rejects_an_action_sealed_with_another_key(executor: Executor) -> None:
    forged = action(ActionKind.MARK_DISPUTED, Tier.T2, {"invoice_id": "inv_0412"}, key=bytes(32))
    with pytest.raises(ExecutionRefused, match="seal"):
        executor.execute(forged, OPERATOR)


@pytest.mark.parametrize(
    "change",
    [
        {"payload": {"invoice_id": "inv_0623"}},
        {"tier": Tier.T0},
        {"blocking_rules": ()},
        {"intent_id": "int_other"},
        {"verified_at": T0 - timedelta(hours=1)},
    ],
)
def test_rejects_an_action_changed_after_sealing(
    executor: Executor, fresh_db: Path, change: dict[str, Any]
) -> None:
    sealed = action(
        ActionKind.MARK_DISPUTED,
        Tier.T2,
        {"invoice_id": "inv_0412"},
        blocking=("PayrollProtection",) if "blocking_rules" in change else (),
    )
    tampered = dataclasses.replace(sealed, **change)
    with pytest.raises(ExecutionRefused, match="seal"):
        executor.execute(tampered, OPERATOR)
    assert invoice_status(fresh_db, "inv_0412") is InvoiceStatus.OVERDUE


def test_rejects_payload_mutated_in_place(executor: Executor) -> None:
    sealed = action(ActionKind.MARK_DISPUTED, Tier.T2, {"invoice_id": "inv_0412"})
    sealed.payload["invoice_id"] = "inv_0623"
    with pytest.raises(ExecutionRefused, match="seal"):
        executor.execute(sealed, OPERATOR)


# ---- BLOCK and the hard-coded T3 kinds


@pytest.mark.parametrize("approval", [None, OPERATOR, Approval(Actor.OWNER, T0)])
def test_block_cannot_be_overridden_by_any_approval(
    executor: Executor, fresh_db: Path, approval: Approval | None
) -> None:
    blocked = action(
        ActionKind.MARK_DISPUTED,
        Tier.T2,
        {"invoice_id": "inv_0412"},
        blocking=("DisputedInvoiceLock",),
    )
    with pytest.raises(ExecutionRefused, match="cannot be overridden"):
        executor.execute(blocked, approval)
    assert invoice_status(fresh_db, "inv_0412") is InvoiceStatus.OVERDUE


@pytest.mark.parametrize("kind", [ActionKind.INITIATE_PAYMENT, ActionKind.ACCEPT_FINANCING_OFFER])
@pytest.mark.parametrize("tier", [Tier.T0, Tier.T1, Tier.T2])
def test_payment_and_financing_acceptance_must_be_t3(
    executor: Executor, kind: ActionKind, tier: Tier
) -> None:
    with pytest.raises(ExecutionRefused, match="must be T3"):
        executor.execute(action(kind, tier), OPERATOR)


# ---- tier gates


def test_t2_needs_a_human_approval(executor: Executor, fresh_db: Path) -> None:
    sealed = action(ActionKind.MARK_DISPUTED, Tier.T2, {"invoice_id": "inv_0412"})
    with pytest.raises(ExecutionRefused, match="requires operator approval"):
        executor.execute(sealed)
    for machine in (Actor.LLM, Actor.SYSTEM):
        with pytest.raises(ExecutionRefused, match="cannot approve"):
            executor.execute(sealed, Approval(machine, T0))
    assert invoice_status(fresh_db, "inv_0412") is InvoiceStatus.OVERDUE


def test_t2_executes_through_the_executor_with_diff_and_audit(
    executor: Executor, fresh_db: Path
) -> None:
    result = executor.execute(
        action(ActionKind.MARK_DISPUTED, Tier.T2, {"invoice_id": "inv_0412"}), OPERATOR
    )
    assert result.changes == [
        {
            "table": "invoice",
            "id": "inv_0412",
            "field": "status",
            "before": "overdue",
            "after": "disputed",
        }
    ]
    assert invoice_status(fresh_db, "inv_0412") is InvoiceStatus.DISPUTED
    with make_readonly_sessionmaker(fresh_db)() as s:
        events = load_events(s)
        assert verify_stored(s).intact
    executed = events[-1]
    assert executed.seq == result.audit_seq
    assert executed.event_type == "action.executed"
    assert executed.actor == "operator"
    assert executed.payload["changes"] == result.changes


def test_failed_handler_changes_nothing(executor: Executor, fresh_db: Path) -> None:
    paid = "inv_0641"  # the legitimate, already-paid Delmar invoice
    with pytest.raises(ExecutionRefused, match="is not a receivable"):
        executor.execute(action(ActionKind.MARK_DISPUTED, Tier.T2, {"invoice_id": paid}), OPERATOR)
    with make_readonly_sessionmaker(fresh_db)() as s:
        events = load_events(s)
    assert [e.event_type for e in events] == ["action.refused"]


def test_t0_runs_without_approval_and_changes_nothing(executor: Executor) -> None:
    result = executor.execute(action(ActionKind.DRAFT_FORECAST, Tier.T0))
    assert result.changes == []


def _t3() -> VerifiedAction:
    return action(ActionKind.ACCEPT_FINANCING_OFFER, Tier.T3, {"product_id": "fin_if_01"})


def test_t3_needs_the_typed_confirmation(executor: Executor, clock: Clock) -> None:
    clock.now = T0 + timedelta(minutes=5)
    late = T0 + timedelta(minutes=5)
    with pytest.raises(ExecutionRefused, match="typed confirmation"):
        executor.execute(_t3(), Approval(Actor.OPERATOR, late))
    with pytest.raises(ExecutionRefused, match="typed confirmation"):
        executor.execute(_t3(), Approval(Actor.OPERATOR, late, "confirm"))


def test_t3_cool_off_is_enforced(executor: Executor, clock: Clock, policy: Policy) -> None:
    phrase = t3_confirmation_phrase(_t3())
    early = T0 + timedelta(seconds=policy.t3.cool_off_seconds - 1)
    clock.now = early
    with pytest.raises(ExecutionRefused, match="cool-off"):
        executor.execute(_t3(), Approval(Actor.OPERATOR, early, phrase))
    # An approval timestamp cannot be backdated past the clock either way.
    ready = T0 + timedelta(seconds=policy.t3.cool_off_seconds)
    clock.now = ready
    with pytest.raises(ExecutionRefused, match="no executor handler"):
        # Every gate passed; the handler for accepting offers arrives with Phase 5.
        executor.execute(_t3(), Approval(Actor.OPERATOR, ready, phrase))


def test_every_refusal_is_audited_and_chain_stays_intact(
    executor: Executor, fresh_db: Path
) -> None:
    for bad in ({}, action(ActionKind.MARK_DISPUTED, Tier.T2, {"invoice_id": "inv_0412"})):
        with pytest.raises(ExecutionRefused):
            executor.execute(bad)
    reasons = [r["reason"] for r in refusals(fresh_db)]
    assert len(reasons) == 2
    with make_readonly_sessionmaker(fresh_db)() as s:
        assert verify_stored(s).intact


def test_sealing_key_file(tmp_path: Path) -> None:
    path = tmp_path / "keys" / "verifier.key"
    key = load_or_create_key(path)
    assert len(key) == 32
    assert load_or_create_key(path) == key
    assert path.stat().st_mode & 0o777 == 0o600
    path.write_text("abcd")
    with pytest.raises(ValueError):
        load_or_create_key(path)
