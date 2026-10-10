"""Turns and approvals: the path from a proposer's output to the ledger.

    raw proposer output ─► Verifier: six stages over a read-only snapshot
                       ─► Executor.store_verification: intent, receipt, audit event
                       ─► T0/T1 verified: Executor.execute now
                          T2/T3 verified: wait in the approval queue
                          blocked or rejected: stop, and the receipt says why

The proposer is anything that returns raw text. Until Phase 4 it is a stub,
and Phase 4 plugs in the model clients. Nothing here trusts that text.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from ledgerguard.db.models import ActionIntentRecord, TrustReceiptRecord
from ledgerguard.domain.enums import ActionKind, Actor, IntentStatus, Tier
from ledgerguard.executor.executor import Approval, ExecutionRefused, ExecutionResult, Executor
from ledgerguard.verifier.pipeline import Verifier
from ledgerguard.verifier.seal import VerifiedAction
from ledgerguard.verifier.snapshot import load_snapshot


@dataclass(frozen=True)
class TurnResult:
    intent_id: str
    outcome: str  # verified | blocked | rejected
    status: str  # the intent's status after this turn
    presented: dict[str, Any]
    receipt: dict[str, Any]
    execution: ExecutionResult | None


def action_from_record(record: ActionIntentRecord) -> VerifiedAction:
    """Rebuild a stored sealed action. The Executor re-checks the seal."""
    data = dict(record.sealed_action or {})
    if not data:
        raise ExecutionRefused(f"{record.id} has no sealed action")
    return VerifiedAction(
        intent_id=data["intent_id"],
        kind=ActionKind(data["kind"]),
        tier=Tier(data["tier"]),
        payload=data["payload"],
        receipt_id=data["receipt_id"],
        verifier_version=data["verifier_version"],
        verified_at=datetime.fromisoformat(data["verified_at"]),
        blocking_rules=tuple(data.get("blocking_rules", ())),
        seal=data.get("seal", ""),
    )


class CopilotService:
    def __init__(
        self,
        read_sessions: sessionmaker[Session],
        verifier: Verifier,
        executor: Executor,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._reads = read_sessions
        self._verifier = verifier
        self._executor = executor
        self._clock = clock

    def submit(self, raw_output: str, conversation_id: str) -> TurnResult:
        """Verify one proposer output, record it, and run it if its tier allows."""
        with self._reads() as session:
            snapshot = load_snapshot(session)
        v = self._verifier.verify(raw_output, snapshot)
        receipt = self._executor.store_verification(
            receipt=v.receipt,
            conversation_id=conversation_id,
            kind=None if v.intent is None else v.intent.kind,
            payload=None if v.intent is None else v.intent.payload.model_dump(mode="json"),
            rationale="" if v.intent is None else v.intent.rationale,
            raw_output=v.raw_output,
            action=v.action,
        )
        execution = None
        status = IntentStatus.REJECTED.value
        if v.action is not None:
            if v.action.tier in (Tier.T0, Tier.T1):
                try:
                    execution = self._executor.execute(v.action)
                    status = IntentStatus.EXECUTED.value
                except ExecutionRefused as refusal:
                    status = IntentStatus.FAILED.value
                    v.presented["message"] = f"Verified, but could not be applied: {refusal}"
            else:
                status = IntentStatus.PENDING_APPROVAL.value
        presented = {**v.presented, "status": status, "audit_seq": receipt.audit_seq}
        return TurnResult(v.intent_id, v.outcome, status, presented, receipt.document(), execution)

    # ---- approval queue

    def pending(self) -> list[dict[str, Any]]:
        with self._reads() as session:
            rows = session.execute(
                select(ActionIntentRecord, TrustReceiptRecord)
                .join(
                    TrustReceiptRecord, TrustReceiptRecord.action_intent_id == ActionIntentRecord.id
                )
                .where(ActionIntentRecord.status == IntentStatus.PENDING_APPROVAL)
                .order_by(ActionIntentRecord.created_at)
            ).all()
            return [
                {
                    "intent_id": intent.id,
                    "kind": None if intent.kind is None else intent.kind.value,
                    "tier": None if receipt.tier is None else receipt.tier.value,
                    "payload": intent.payload,
                    "rationale": intent.proposer_rationale,
                    "risk_flags": receipt.risk_flags,
                    "rules_evaluated": receipt.rules_evaluated,
                    "approval": receipt.body.get("approval"),
                    "payload_figures": receipt.body.get("payload_figures"),
                    "receipt_id": receipt.id,
                }
                for intent, receipt in rows
            ]

    def approve(
        self, intent_id: str, approver: Actor, confirmation: str | None = None
    ) -> ExecutionResult:
        """Approve a pending action, after re-checking it against the ledger as it is now."""
        with self._reads() as session:
            record = session.get(ActionIntentRecord, intent_id)
            if record is None:
                raise ExecutionRefused(f"{intent_id} not found")
            action = action_from_record(record)
            pending = record.status is IntentStatus.PENDING_APPROVAL
            snapshot = load_snapshot(session)
        if pending:
            reasons = self._verifier.recheck(action.kind.value, action.payload, snapshot)
            if reasons:
                text = "the ledger changed since verification and this is now blocked: " + (
                    "; ".join(reasons)
                )
                self._executor.close(intent_id, IntentStatus.REJECTED, text)
                raise ExecutionRefused(text)
        return self._executor.execute(action, Approval(approver, self._clock(), confirmation))

    def decline(self, intent_id: str, by: Actor, reason: str) -> int:
        return self._executor.decline(intent_id, by, reason)

    def receipt(self, receipt_id: str) -> dict[str, Any] | None:
        with self._reads() as session:
            row = session.get(TrustReceiptRecord, receipt_id)
            return None if row is None else dict(row.body)
