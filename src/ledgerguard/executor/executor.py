"""The Executor: the only component that holds a writable database connection.

Two kinds of write pass through here:

* ``execute``: a ledger change, accepted only as a sealed ``VerifiedAction``,
  with the approval its tier requires. The change and its audit event are
  committed in one transaction, or neither is.
* ``record``: an append to the audit log. The log is append-only, and the
  database itself refuses UPDATE and DELETE on it (migration 0002).

Every refusal is itself written to the audit log.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from sqlalchemy import Engine, create_engine, event, select
from sqlalchemy.orm import Session

from ledgerguard.audit.chain import GENESIS, chain_hash, event_content
from ledgerguard.config import Policy
from ledgerguard.db.models import AuditEvent, Invoice
from ledgerguard.domain.enums import ActionKind, Actor, InvoiceStatus, InvoiceType, Tier
from ledgerguard.verifier.seal import Sealer, VerifiedAction

Clock = Callable[[], datetime]
Change = dict[str, Any]  # {"table", "id", "field", "before", "after"}

# Kinds that only read or simulate: executing them records the event, changes no rows.
NON_MUTATING = frozenset(
    {
        ActionKind.ANSWER_QUERY,
        ActionKind.DRAFT_FORECAST,
        ActionKind.RUN_SCENARIO,
        ActionKind.FLAG_ANOMALY,
        ActionKind.RANK_FINANCING,
    }
)
HUMAN_APPROVERS = frozenset({Actor.OPERATOR, Actor.OWNER})


class ExecutionRefused(Exception):
    """The Executor would not run this action. The reason is in the audit log too."""


@dataclass(frozen=True)
class Approval:
    approver: Actor
    approved_at: datetime
    confirmation: str | None = None  # typed second confirmation, required for T3


@dataclass(frozen=True)
class ExecutionResult:
    intent_id: str
    changes: list[Change]
    audit_seq: int


def t3_confirmation_phrase(action: VerifiedAction) -> str:
    """What the operator must type to confirm an external or irreversible action."""
    return f"CONFIRM {action.intent_id}"


def _open_writable(db_path: Path) -> Engine:
    """The one runtime writable engine. Serialised writers via BEGIN IMMEDIATE."""
    engine = create_engine(f"sqlite:///{db_path.resolve()}", connect_args={"isolation_level": None})

    @event.listens_for(engine, "connect")
    def _on_connect(dbapi_connection: Any, _record: Any) -> None:
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    @event.listens_for(engine, "begin")
    def _begin_immediate(conn: Any) -> None:
        conn.exec_driver_sql("BEGIN IMMEDIATE")

    return engine


# --------------------------------------------------------------------------- handlers

Handler = Callable[[Session, VerifiedAction], list[Change]]


def _mark_disputed(session: Session, action: VerifiedAction) -> list[Change]:
    invoice_id = action.payload.get("invoice_id")
    if not isinstance(invoice_id, str):
        raise ExecutionRefused("MARK_DISPUTED payload needs an invoice_id")
    invoice = session.get(Invoice, invoice_id)
    if invoice is None or invoice.type is not InvoiceType.RECEIVABLE:
        raise ExecutionRefused(f"{invoice_id} is not a receivable invoice")
    if invoice.status in (InvoiceStatus.PAID, InvoiceStatus.DISPUTED):
        raise ExecutionRefused(f"{invoice_id} is {invoice.status.value}; cannot mark disputed")
    before = invoice.status
    invoice.status = InvoiceStatus.DISPUTED
    return [
        {
            "table": "invoice",
            "id": invoice_id,
            "field": "status",
            "before": before.value,
            "after": InvoiceStatus.DISPUTED.value,
        }
    ]


HANDLERS: dict[ActionKind, Handler] = {
    ActionKind.MARK_DISPUTED: _mark_disputed,
}


# --------------------------------------------------------------------------- executor


class Executor:
    def __init__(
        self,
        db_path: Path,
        sealer: Sealer,
        policy: Policy,
        clock: Clock = lambda: datetime.now(UTC),
    ) -> None:
        self._engine = _open_writable(db_path)
        self._sealer = sealer
        self._policy = policy
        self._clock = clock

    def dispose(self) -> None:
        self._engine.dispose()

    # ---- audit

    def record(self, actor: Actor, event_type: str, payload: dict[str, Any]) -> int:
        """Append one event to the audit log; returns its sequence number."""
        with Session(self._engine) as session, session.begin():
            return self._append(session, actor, event_type, payload)

    def _append(
        self, session: Session, actor: Actor, event_type: str, payload: dict[str, Any]
    ) -> int:
        head = session.execute(
            select(AuditEvent.seq, AuditEvent.hash).order_by(AuditEvent.seq.desc()).limit(1)
        ).first()
        seq, prev = (head[0] + 1, head[1]) if head else (1, GENESIS)
        occurred_at = self._clock()
        event_id = f"evt_{seq:07d}"
        content = event_content(
            id=event_id,
            seq=seq,
            occurred_at=occurred_at,
            actor=actor.value,
            event_type=event_type,
            payload=payload,
        )
        session.add(
            AuditEvent(
                id=event_id,
                seq=seq,
                occurred_at=occurred_at,
                actor=actor,
                event_type=event_type,
                payload=content["payload"],
                prev_hash=prev,
                hash=chain_hash(prev, content),
            )
        )
        session.flush()
        return seq

    # ---- ledger changes

    def execute(self, action: object, approval: Approval | None = None) -> ExecutionResult:
        try:
            checked = self._admit(action, approval)
        except ExecutionRefused as refusal:
            self.record(
                Actor.SYSTEM,
                "action.refused",
                {
                    "intent_id": getattr(action, "intent_id", None)
                    if isinstance(action, VerifiedAction)
                    else None,
                    "received_type": type(action).__name__,
                    "reason": str(refusal),
                },
            )
            raise

        try:
            with Session(self._engine) as session, session.begin():
                changes = self._apply(session, checked)
                seq = self._append(
                    session,
                    approval.approver if approval else Actor.SYSTEM,
                    "action.executed",
                    {
                        "intent_id": checked.intent_id,
                        "kind": checked.kind.value,
                        "tier": checked.tier.value,
                        "receipt_id": checked.receipt_id,
                        "approved_by": approval.approver.value if approval else None,
                        "changes": changes,
                    },
                )
        except ExecutionRefused as refusal:
            # The transaction above has rolled back: no partial change survives.
            self.record(
                Actor.SYSTEM,
                "action.refused",
                {
                    "intent_id": checked.intent_id,
                    "received_type": "VerifiedAction",
                    "reason": str(refusal),
                },
            )
            raise
        return ExecutionResult(checked.intent_id, changes, seq)

    def _admit(self, action: object, approval: Approval | None) -> VerifiedAction:
        if not isinstance(action, VerifiedAction):
            raise ExecutionRefused(
                f"only a VerifiedAction can be executed, got {type(action).__name__}"
            )
        if not self._sealer.is_valid(action):
            raise ExecutionRefused(
                "seal missing or invalid: not issued by the verifier, or changed after verification"
            )
        if action.blocking_rules:
            raise ExecutionRefused(
                f"blocked by {', '.join(action.blocking_rules)}; BLOCK cannot be overridden"
            )
        if (
            action.kind in (ActionKind.INITIATE_PAYMENT, ActionKind.ACCEPT_FINANCING_OFFER)
            and action.tier is not Tier.T3
        ):
            raise ExecutionRefused(f"{action.kind.value} must be T3")
        if action.tier in (Tier.T2, Tier.T3):
            self._check_approval(action, approval)
        return action

    def _check_approval(self, action: VerifiedAction, approval: Approval | None) -> None:
        if approval is None:
            raise ExecutionRefused(f"{action.tier.value} requires operator approval")
        if approval.approver not in HUMAN_APPROVERS:
            raise ExecutionRefused(f"{approval.approver.value} cannot approve actions")
        if action.tier is Tier.T3:
            if approval.confirmation != t3_confirmation_phrase(action):
                raise ExecutionRefused("T3 requires the typed confirmation phrase")
            ready_at = action.verified_at + timedelta(seconds=self._policy.t3.cool_off_seconds)
            if approval.approved_at < ready_at or self._clock() < ready_at:
                raise ExecutionRefused(f"T3 cool-off: approval allowed from {ready_at.isoformat()}")

    def _apply(self, session: Session, action: VerifiedAction) -> list[Change]:
        if action.kind in NON_MUTATING:
            return []
        handler = HANDLERS.get(action.kind)
        if handler is None:
            raise ExecutionRefused(f"no executor handler for {action.kind.value} yet")
        return handler(session, action)
