"""Reading the audit log through a read-only session."""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from ledgerguard.audit.chain import ChainReport, StoredEvent, timestamp, verify
from ledgerguard.db.models import AuditEvent


def _stored(row: AuditEvent) -> StoredEvent:
    return StoredEvent(
        id=row.id,
        seq=row.seq,
        occurred_at=row.occurred_at,
        actor=row.actor.value,
        event_type=row.event_type,
        payload=row.payload,
        prev_hash=row.prev_hash,
        hash=row.hash,
    )


def load_events(session: Session, from_seq: int = 1, limit: int | None = None) -> list[StoredEvent]:
    query = select(AuditEvent).where(AuditEvent.seq >= from_seq).order_by(AuditEvent.seq)
    if limit is not None:
        query = query.limit(limit)
    return [_stored(row) for row in session.scalars(query)]


def verify_stored(session: Session) -> ChainReport:
    return verify(load_events(session))


def event_dict(ev: StoredEvent) -> dict[str, Any]:
    return {
        "id": ev.id,
        "seq": ev.seq,
        "occurred_at": timestamp(ev.occurred_at),
        "actor": ev.actor,
        "event_type": ev.event_type,
        "payload": ev.payload,
        "prev_hash": ev.prev_hash,
        "hash": ev.hash,
    }
