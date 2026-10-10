"""Stage 5: policy.

* **Financing.** Any financing content carries a fixed disclosure that the
  model cannot remove. A financing product may only be discussed alongside
  its total cost of capital in currency over the real term, recomputed by the
  verifier. Otherwise the action is blocked.
* **No guaranteed outcomes.** Guarantee-shaped phrases are masked visibly, and
  for advisory kinds the intent is flagged.
* **Accepting a financing offer** is always T3 and never auto-executed. That
  is enforced by tier assignment and, again, by the Executor.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from ledgerguard.domain.enums import ActionKind
from ledgerguard.verifier.intent import AcceptFinancingOffer, ActionIntent
from ledgerguard.verifier.recompute import Recomputation
from ledgerguard.verifier.snapshot import table_of

FINANCING_DISCLOSURE = (
    "Informational only: this is not regulated financial advice. Products in this "
    "sandbox are synthetic. Compare total cost of capital and terms with a qualified "
    "adviser before committing."
)
GUARANTEE_MARKER = "[removed: guaranteed-outcome language]"
FINANCING_KINDS = frozenset({ActionKind.RANK_FINANCING, ActionKind.ACCEPT_FINANCING_OFFER})
ADVISORY_KINDS = frozenset(
    {ActionKind.ANSWER_QUERY, ActionKind.DRAFT_FORECAST, ActionKind.RUN_SCENARIO, *FINANCING_KINDS}
)
# Formulas that state a product's total cost of capital in currency. Phase 5 adds them.
TOTAL_COST_FORMULAS: frozenset[str] = frozenset()

# One alternation, applied in a single pass, so a replacement is never re-scanned.
GUARANTEE_PATTERN = re.compile(
    "|".join(
        (
            r"\b(?:will|would|is going to)\s+(?:definitely\s+|certainly\s+|surely\s+)?"
            r"(?:fix|solve|resolve|cure|eliminate|end|guarantee)\b[^.;]*",
            r"\bguarantee(?:d|s)?\b",
            r"\brisk[- ]free\b",
            r"\bno risk\b",
            r"\b(?:100|one hundred) ?% (?:safe|certain|sure)\b",
            r"\bcan(?:not|'t) (?:go wrong|fail)\b",
            r"\bcertain(?:ly)? to (?:work|succeed|cover|fix)\b",
            r"\bnever (?:run out|be short)\b",
        )
    ),
    re.IGNORECASE,
)


@dataclass(frozen=True)
class PolicyFinding:
    check: str
    result: str  # ok | corrected | violation
    detail: str

    def as_dict(self) -> dict[str, Any]:
        return {"check": self.check, "result": self.result, "detail": self.detail}


def mask_guarantees(text: str) -> tuple[str, list[str]]:
    found: list[str] = []

    def _mask(m: re.Match[str]) -> str:
        found.append(m.group(0))
        return GUARANTEE_MARKER

    return GUARANTEE_PATTERN.sub(_mask, text), found


def financing_products_discussed(intent: ActionIntent) -> set[str]:
    products = {
        r for c in intent.claims for r in c.source_row_ids if table_of(r) == "financing_product"
    }
    if isinstance(intent.payload, AcceptFinancingOffer):
        products.add(intent.payload.product_id)
    return products


def check_financing(
    intent: ActionIntent, recomputations: list[Recomputation]
) -> tuple[list[PolicyFinding], bool]:
    """Returns findings and whether the financing disclosure applies."""
    products = financing_products_discussed(intent)
    if intent.kind not in FINANCING_KINDS and not products:
        return [], False
    costed = {
        operand
        for claim, rc in zip(intent.claims, recomputations, strict=True)
        if claim.derivation is not None
        and claim.derivation.formula.value in TOTAL_COST_FORMULAS
        and rc.status in ("match", "mismatch")
        for operand in claim.derivation.operands
    }
    findings = [PolicyFinding("financing_disclosure", "ok", "non-removable disclosure attached")]
    uncosted = sorted(products - costed)
    if uncosted:
        findings.append(
            PolicyFinding(
                "total_cost_of_capital",
                "violation",
                f"{', '.join(uncosted)} discussed without a verified total cost of capital over "
                "the full term",
            )
        )
    elif products:
        findings.append(
            PolicyFinding(
                "total_cost_of_capital", "ok", "every product discussed has a verified total cost"
            )
        )
    return findings, True
