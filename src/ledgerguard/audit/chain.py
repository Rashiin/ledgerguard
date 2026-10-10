"""Pure hashing and verification for the audit chain.

    hash = sha256(prev_hash || canonical_json(event))

Here the event is its own content: id, sequence number, timestamp, actor,
type and payload. The stored ``prev_hash`` and ``hash`` are not part of it. The first
event links to ``GENESIS``. Changing, removing, inserting or reordering any
event breaks the chain from that point on.
"""

from __future__ import annotations

import hashlib
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from ledgerguard.db.types import canonical_json

GENESIS = "0" * 64


def timestamp(value: datetime) -> str:
    """The one textual form of a timestamp, used for both storage and hashing."""
    if value.tzinfo is None:
        raise ValueError("audit timestamps must be timezone-aware")
    return value.astimezone(UTC).isoformat(timespec="microseconds")


def event_content(
    *,
    id: str,
    seq: int,
    occurred_at: datetime,
    actor: str,
    event_type: str,
    payload: Mapping[str, Any],
) -> dict[str, Any]:
    return {
        "id": id,
        "seq": seq,
        "occurred_at": timestamp(occurred_at),
        "actor": actor,
        "event_type": event_type,
        "payload": dict(payload),
    }


def chain_hash(prev_hash: str, content: Mapping[str, Any]) -> str:
    return hashlib.sha256((prev_hash + canonical_json(content)).encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class StoredEvent:
    id: str
    seq: int
    occurred_at: datetime
    actor: str
    event_type: str
    payload: Mapping[str, Any]
    prev_hash: str
    hash: str


@dataclass(frozen=True)
class ChainReport:
    intact: bool
    events: int
    head_hash: str
    first_bad_seq: int | None = None
    reason: str | None = None

    def as_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "intact": self.intact,
            "events": self.events,
            "head_hash": self.head_hash,
        }
        if not self.intact:
            out["first_bad_seq"] = self.first_bad_seq
            out["reason"] = self.reason
        return out


def _check(ev: StoredEvent, expected_seq: int, prev: str) -> str | None:
    """Why ``ev`` does not continue a chain whose head hash is ``prev``, or None."""
    if ev.seq != expected_seq:
        return f"sequence gap: expected {expected_seq}, found {ev.seq}"
    if ev.prev_hash != prev:
        return "prev_hash does not match the previous event's hash"
    content = event_content(
        id=ev.id,
        seq=ev.seq,
        occurred_at=ev.occurred_at,
        actor=ev.actor,
        event_type=ev.event_type,
        payload=ev.payload,
    )
    if chain_hash(prev, content) != ev.hash:
        return "event content does not match its hash"
    return None


def verify(events: Iterable[StoredEvent]) -> ChainReport:
    """Walk the chain in sequence order and recompute every link."""
    prev = GENESIS
    count = 0
    for expected_seq, ev in enumerate(sorted(events, key=lambda e: e.seq), start=1):
        count += 1
        reason = _check(ev, expected_seq, prev)
        if reason is not None:
            return ChainReport(False, count, prev, ev.seq, reason)
        prev = ev.hash
    return ChainReport(True, count, prev)
