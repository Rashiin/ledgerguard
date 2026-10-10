"""Sealing: how the Executor knows an action really came out of the verifier.

A ``VerifiedAction`` carries an HMAC-SHA256 seal over its canonical content,
keyed with a secret held on the owner's machine. The Executor recomputes the
seal and refuses the action on any mismatch. So it refuses:

* any object that is not a ``VerifiedAction`` (a raw ``ActionIntent``, a dict,
  JSON from the model);
* a ``VerifiedAction`` built without the key;
* a sealed action whose amount, target, tier or rule outcome was changed
  after verification, for example while it waited in the approval queue.

This is a guard against bypass by mistake and against tampering with stored
pending actions. It is not a defence against arbitrary code running inside
the application process, which could read the key; that is listed as a
residual risk.
"""

from __future__ import annotations

import hashlib
import hmac
import os
import secrets
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from ledgerguard.audit.chain import timestamp
from ledgerguard.db.types import canonical_json
from ledgerguard.domain.enums import ActionKind, Tier

KEY_BYTES = 32


def load_or_create_key(path: Path) -> bytes:
    """Read the sealing key, creating it (owner-readable only) on first use."""
    if path.exists():
        key = bytes.fromhex(path.read_text(encoding="ascii").strip())
        if len(key) != KEY_BYTES:
            raise ValueError(f"sealing key at {path} has the wrong length")
        return key
    path.parent.mkdir(parents=True, exist_ok=True)
    key = secrets.token_bytes(KEY_BYTES)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w", encoding="ascii") as fh:
        fh.write(key.hex())
    return key


@dataclass(frozen=True)
class VerifiedAction:
    """An action that passed every verifier stage. Only the verifier creates these."""

    intent_id: str
    kind: ActionKind
    tier: Tier
    payload: dict[str, Any]
    receipt_id: str
    verifier_version: str
    verified_at: datetime  # start of the T3 cool-off
    # Names of rules that returned BLOCK. The verifier should never seal a blocked
    # action; the Executor refuses one anyway (defence in depth).
    blocking_rules: tuple[str, ...] = ()
    seal: str = field(default="", compare=False)

    def content(self) -> dict[str, Any]:
        return {
            "intent_id": self.intent_id,
            "kind": self.kind.value,
            "tier": self.tier.value,
            "payload": self.payload,
            "receipt_id": self.receipt_id,
            "verifier_version": self.verifier_version,
            "verified_at": timestamp(self.verified_at),
            "blocking_rules": list(self.blocking_rules),
        }


class Sealer:
    def __init__(self, key: bytes) -> None:
        if len(key) != KEY_BYTES:
            raise ValueError("sealing key must be 32 bytes")
        self._key = key

    def _mac(self, action: VerifiedAction) -> str:
        message = canonical_json(action.content()).encode("utf-8")
        return hmac.new(self._key, message, hashlib.sha256).hexdigest()

    def seal(self, action: VerifiedAction) -> VerifiedAction:
        return VerifiedAction(**{**action.__dict__, "seal": self._mac(action)})

    def is_valid(self, action: VerifiedAction) -> bool:
        return bool(action.seal) and hmac.compare_digest(action.seal, self._mac(action))
