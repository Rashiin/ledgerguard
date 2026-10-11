"""The Trust Receipt records what the verifier did, so nobody has to trust the model."""

from __future__ import annotations

import hashlib
from typing import Any

from pydantic import BaseModel, ConfigDict

from ledgerguard.db.types import canonical_json


class Approval(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    required: bool
    by: str | None  # who may approve
    status: str  # not_required | pending | not_applicable
    confirmation_phrase: str | None = None  # T3: what must be typed
    not_before: str | None = None  # T3: end of the cool-off


class TrustReceipt(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    receipt_id: str
    action_intent_id: str
    outcome: str  # verified | blocked | rejected
    kind: str | None
    tier: str | None
    rows_read: list[dict[str, str]]
    schema_errors: list[dict[str, str]]
    recomputations: list[dict[str, Any]]
    provenance: list[dict[str, Any]]
    rules_evaluated: list[dict[str, Any]]
    policy: list[dict[str, Any]]
    text_corrections: list[dict[str, str]]
    payload_figures: dict[str, str]
    # Stage 4b: mandatory facts the response left out, which the verifier put back.
    material_omissions: list[dict[str, str]] = []
    # The tools the orchestrator ran this turn. They decide which facts were mandatory.
    tools_called: list[str] = []
    risk_flags: list[str]
    disclosures: list[str]
    approval: Approval
    verifier_version: str
    created_at: str
    audit_seq: int | None = None

    def document(self) -> dict[str, Any]:
        return self.model_dump(mode="json")

    def digest(self) -> str:
        """SHA-256 of the receipt as recorded, excluding the audit sequence it is recorded at."""
        body = self.document()
        body.pop("audit_seq", None)
        return hashlib.sha256(canonical_json(body).encode("utf-8")).hexdigest()
