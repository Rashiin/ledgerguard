"""Stage 4: provenance (BUILD_SPEC 6.2).

Every claim must cite at least one existing row, of a table that can support
it. A claim that fails is not deleted silently: it is replaced, in what the
user sees, by a visible marker saying the verifier removed it and why. A
claim whose figure could not be recomputed keeps its statement but loses the
figure.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ledgerguard.verifier.intent import Claim
from ledgerguard.verifier.recompute import FORMULAS, Recomputation
from ledgerguard.verifier.snapshot import LedgerSnapshot, table_of

# Context rows any claim may cite alongside its formula's operand tables.
ALWAYS_SUPPORTING = frozenset({"counterparty", "business"})
REMOVED = "[unsupported claim removed by verifier: {reason}]"


@dataclass(frozen=True)
class ProvenanceFinding:
    claim_index: int
    status: str  # supported | removed | figure_removed
    reason: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {"claim_index": self.claim_index, "status": self.status, "reason": self.reason}


def check_claim(
    snap: LedgerSnapshot, index: int, claim: Claim, recomputation: Recomputation, reads: set[str]
) -> ProvenanceFinding:
    if not claim.source_row_ids:
        return ProvenanceFinding(index, "removed", "it cites no source rows")
    missing = [r for r in claim.source_row_ids if snap.row(r) is None]
    if missing:
        return ProvenanceFinding(
            index, "removed", f"it cites rows that do not exist: {', '.join(missing)}"
        )
    reads.update(claim.source_row_ids)
    if claim.derivation is not None:
        allowed = FORMULAS[claim.derivation.formula].tables | ALWAYS_SUPPORTING
        wrong = [r for r in claim.source_row_ids if table_of(r) not in allowed]
        if wrong:
            return ProvenanceFinding(
                index,
                "removed",
                f"{', '.join(wrong)} cannot support a {claim.derivation.formula.value} figure",
            )
    if recomputation.status == "not_recomputable":
        return ProvenanceFinding(index, "figure_removed", recomputation.reason)
    return ProvenanceFinding(index, "supported")
