"""The Executor: the only component that holds a writable database connection.

Writes that pass through here:

* ``execute``: a ledger change. It is accepted only as a sealed
  ``VerifiedAction`` whose intent is on record and still open, with the
  approval its tier requires. The change, the intent's new status and the
  audit event commit in one transaction, or none of them do. A sealed action
  runs at most once.
* ``store_verification``: the verifier's records (the intent, its Trust
  Receipt and an audit event), written together.
* ``decline``: an operator refuses a pending action.
* ``record``: an append to the audit log. The log is append-only, and the
  database itself refuses UPDATE and DELETE on it (migration 0002).

Every refusal is itself written to the audit log.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

from sqlalchemy import Engine, create_engine, event, func, select
from sqlalchemy.orm import Session

from ledgerguard.audit.chain import GENESIS, chain_hash, event_content
from ledgerguard.config import Policy
from ledgerguard.db.models import (
    Account,
    ActionIntentRecord,
    AuditEvent,
    Business,
    Invoice,
    Transaction,
    TrustReceiptRecord,
)
from ledgerguard.domain.enums import (
    AccountType,
    ActionKind,
    Actor,
    Direction,
    IntentStatus,
    InvoiceStatus,
    InvoiceType,
    Tier,
    TransactionSource,
)
from ledgerguard.domain.tax import vat_on
from ledgerguard.money import to_money
from ledgerguard.verifier.receipt import TrustReceipt
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
OPEN_STATUSES = frozenset({IntentStatus.VERIFIED, IntentStatus.PENDING_APPROVAL})


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


def _change(table: str, row_id: str, field: str, before: Any, after: Any) -> Change:
    def text(v: Any) -> Any:
        return v.value if hasattr(v, "value") else (str(v) if isinstance(v, Decimal | date) else v)

    return {
        "table": table,
        "id": row_id,
        "field": field,
        "before": text(before),
        "after": text(after),
    }


def _resolved_money(action: VerifiedAction, key: str) -> Decimal:
    try:
        return to_money(action.payload["resolved"][key])
    except (KeyError, TypeError) as exc:
        raise ExecutionRefused(f"sealed action has no verifier-computed {key}") from exc


def _next_id(session: Session, model: type[Transaction] | type[Invoice], prefix: str) -> str:
    highest = session.scalar(select(func.max(model.id)).where(model.id.like(f"{prefix}_%")))
    number = int(highest.split("_")[1]) + 1 if highest else 1
    return f"{prefix}_{number:04d}"


def _operating_account(session: Session) -> Account:
    account = session.scalars(
        select(Account).where(Account.type == AccountType.BANK).order_by(Account.id)
    ).first()
    if account is None:
        raise ExecutionRefused("no bank account")
    return account


def _ledger_date(session: Session) -> date:
    return session.scalars(select(Business.data_as_of)).one()


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
    return [_change("invoice", invoice_id, "status", before, invoice.status)]


def _settle(
    session: Session,
    invoice: Invoice,
    amount: Decimal,
    direction: Direction,
    booked_on: date,
    category: str,
) -> list[Change]:
    txn_id = _next_id(session, Transaction, "txn")
    session.add(
        Transaction(
            id=txn_id,
            account_id=_operating_account(session).id,
            booked_on=booked_on,
            value_date=booked_on,
            amount=amount,
            direction=direction,
            counterparty_id=invoice.counterparty_id,
            category=category,
            description=f"{'Receipt' if direction is Direction.IN else 'Payment'} - "
            f"{invoice.id} (sandbox, via LedgerGuard)",
            source=TransactionSource.MANUAL,
            reconciled=False,
            invoice_id=invoice.id,
        )
    )
    before_paid, before_status = invoice.paid_amount, invoice.status
    invoice.paid_amount = before_paid + amount
    invoice.status = (
        InvoiceStatus.PAID
        if invoice.paid_amount == invoice.gross_amount
        else InvoiceStatus.PART_PAID
    )
    return [
        _change(
            "transaction",
            txn_id,
            "*",
            None,
            f"{direction.value} {amount} {invoice.counterparty_id} on {booked_on}",
        ),
        _change("invoice", invoice.id, "paid_amount", before_paid, invoice.paid_amount),
        _change("invoice", invoice.id, "status", before_status, invoice.status),
    ]


def _record_payment(session: Session, action: VerifiedAction) -> list[Change]:
    invoice = session.get(Invoice, action.payload.get("invoice_id", ""))
    if invoice is None or invoice.type is not InvoiceType.RECEIVABLE:
        raise ExecutionRefused("RECORD_PAYMENT needs an existing receivable")
    if invoice.gross_amount - invoice.paid_amount != _resolved_money(action, "outstanding_before"):
        raise ExecutionRefused(f"{invoice.id} changed since verification; verify again")
    received = action.payload.get("received_on")
    booked_on = date.fromisoformat(received) if received else _ledger_date(session)
    return _settle(
        session,
        invoice,
        _resolved_money(action, "recorded_amount"),
        Direction.IN,
        booked_on,
        "customer_receipt",
    )


def _initiate_payment(session: Session, action: VerifiedAction) -> list[Change]:
    resolved = action.payload.get("resolved", {})
    payments = resolved.get("payments") or []
    if not payments:
        raise ExecutionRefused("sealed payment has no verifier-computed payments")
    pay_on = date.fromisoformat(resolved["pay_on"])
    changes: list[Change] = []
    for pay in payments:
        invoice = session.get(Invoice, pay["invoice_id"])
        amount = to_money(pay["amount"])
        if invoice is None or invoice.type is not InvoiceType.PAYABLE:
            raise ExecutionRefused(f"{pay['invoice_id']} is not a payable invoice")
        if invoice.gross_amount - invoice.paid_amount != amount:
            raise ExecutionRefused(f"{invoice.id} changed since verification; verify again")
        changes += _settle(session, invoice, amount, Direction.OUT, pay_on, "supplier_payment")
    return changes


def _draft_invoice(session: Session, action: VerifiedAction) -> list[Change]:
    """T1: a draft only. It moves no money and is not sent to anyone."""
    p = action.payload
    net = _resolved_money(action, "net_amount")
    issued = _ledger_date(session)
    invoice_id = _next_id(session, Invoice, "inv")
    tax = vat_on(net)
    session.add(
        Invoice(
            id=invoice_id,
            type=InvoiceType.RECEIVABLE,
            counterparty_id=p["counterparty_id"],
            issued_on=issued,
            due_on=issued + timedelta(days=int(p["due_in_days"])),
            gross_amount=net + tax,
            tax_amount=tax,
            net_amount=net,
            status=InvoiceStatus.DRAFT,
            paid_amount=Decimal("0.00"),
            description=str(p["description"]),
        )
    )
    return [
        _change(
            "invoice",
            invoice_id,
            "*",
            None,
            f"draft receivable {net} + VAT {tax} to {p['counterparty_id']}",
        )
    ]


HANDLERS: dict[ActionKind, Handler] = {
    ActionKind.DRAFT_INVOICE: _draft_invoice,
    ActionKind.MARK_DISPUTED: _mark_disputed,
    ActionKind.RECORD_PAYMENT: _record_payment,
    ActionKind.INITIATE_PAYMENT: _initiate_payment,
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

    # ---- verifier records

    def store_verification(
        self,
        *,
        receipt: TrustReceipt,
        conversation_id: str,
        kind: ActionKind | None,
        payload: dict[str, Any] | None,
        rationale: str,
        raw_output: str | None,
        action: VerifiedAction | None,
    ) -> TrustReceipt:
        """Persist the intent, its receipt and an audit event; returns the receipt with
        its audit sequence number filled in."""
        if action is not None and not self._sealer.is_valid(action):
            raise ExecutionRefused("refusing to store an action with an invalid seal")
        if receipt.outcome == "verified":
            status = (
                IntentStatus.PENDING_APPROVAL
                if receipt.approval.required
                else IntentStatus.VERIFIED
            )
        else:
            status = IntentStatus.REJECTED
        with Session(self._engine) as session, session.begin():
            seq = self._append(
                session,
                Actor.SYSTEM,
                f"intent.{receipt.outcome}",
                {
                    "intent_id": receipt.action_intent_id,
                    "receipt_id": receipt.receipt_id,
                    "receipt_sha256": receipt.digest(),
                    "kind": receipt.kind,
                    "tier": receipt.tier,
                    "risk_flags": receipt.risk_flags,
                    "blocked_by": [
                        r["rule"] for r in receipt.rules_evaluated if r["result"] == "BLOCK"
                    ],
                },
            )
            stored = receipt.model_copy(update={"audit_seq": seq})
            session.add(
                ActionIntentRecord(
                    id=receipt.action_intent_id,
                    conversation_id=conversation_id,
                    created_at=self._clock(),
                    kind=kind,
                    payload=payload,
                    proposer_rationale=rationale,
                    status=status,
                    raw_output=None if raw_output is None else raw_output[:8000],
                    sealed_action=None
                    if action is None
                    else {**action.content(), "seal": action.seal},
                )
            )
            session.flush()
            session.add(
                TrustReceiptRecord(
                    id=receipt.receipt_id,
                    action_intent_id=receipt.action_intent_id,
                    rows_read=stored.rows_read,
                    recomputations=stored.recomputations,
                    rules_evaluated=stored.rules_evaluated,
                    tier=None if receipt.tier is None else Tier(receipt.tier),
                    risk_flags=stored.risk_flags,
                    verifier_version=receipt.verifier_version,
                    created_at=self._clock(),
                    outcome=receipt.outcome,
                    body=stored.document(),
                    audit_seq=seq,
                )
            )
        return stored

    def decline(self, intent_id: str, by: Actor, reason: str) -> int:
        if by not in HUMAN_APPROVERS:
            raise ExecutionRefused(f"{by.value} cannot decline actions")
        with Session(self._engine) as session, session.begin():
            record = session.get(ActionIntentRecord, intent_id)
            if record is None or record.status is not IntentStatus.PENDING_APPROVAL:
                raise ExecutionRefused(f"{intent_id} is not awaiting approval")
            record.status = IntentStatus.DECLINED
            return self._append(
                session, by, "approval.declined", {"intent_id": intent_id, "reason": reason}
            )

    # ---- ledger changes

    def execute(self, action: object, approval: Approval | None = None) -> ExecutionResult:
        try:
            checked = self._admit(action, approval)
        except ExecutionRefused as refusal:
            self._refused(action, refusal)
            raise
        applying = False
        try:
            with Session(self._engine) as session, session.begin():
                record = session.get(ActionIntentRecord, checked.intent_id)
                if record is None:
                    raise ExecutionRefused(f"{checked.intent_id} has no verification record")
                if record.status not in OPEN_STATUSES:
                    raise ExecutionRefused(
                        f"{checked.intent_id} is {record.status.value}; a verified action "
                        "runs at most once"
                    )
                applying = True
                changes = self._apply(session, checked)
                record.status = IntentStatus.EXECUTED
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
            self._refused(checked, refusal)
            if applying:  # the handler refused: this action cannot run, close it
                self.close(checked.intent_id, IntentStatus.FAILED, str(refusal))
            raise
        return ExecutionResult(checked.intent_id, changes, seq)

    def close(self, intent_id: str, status: IntentStatus, reason: str) -> None:
        """Move an open intent to a final state (failed, or rejected on re-check)."""
        if status not in (IntentStatus.FAILED, IntentStatus.REJECTED):
            raise ValueError("close() only records failure or rejection")
        with Session(self._engine) as session, session.begin():
            record = session.get(ActionIntentRecord, intent_id)
            if record is None or record.status not in OPEN_STATUSES:
                return
            record.status = status
            self._append(
                session,
                Actor.SYSTEM,
                f"intent.{status.value}",
                {"intent_id": intent_id, "reason": reason},
            )

    def _refused(self, action: object, refusal: ExecutionRefused) -> None:
        self.record(
            Actor.SYSTEM,
            "action.refused",
            {
                "intent_id": action.intent_id if isinstance(action, VerifiedAction) else None,
                "received_type": type(action).__name__,
                "reason": str(refusal),
            },
        )

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
