"""Stage 6: permission tiers (BUILD_SPEC 6.3).

A deterministic function of the action kind and the verifier-computed amount.
The model never chooses a tier; a tier in its output is discarded at parse
time.
"""

from __future__ import annotations

from collections.abc import Mapping
from decimal import Decimal

from ledgerguard.domain.enums import ActionKind, Tier

BASE_TIER: dict[ActionKind, Tier] = {
    ActionKind.ANSWER_QUERY: Tier.T0,
    ActionKind.DRAFT_FORECAST: Tier.T0,
    ActionKind.FLAG_ANOMALY: Tier.T0,
    ActionKind.RUN_SCENARIO: Tier.T1,
    ActionKind.DRAFT_INVOICE: Tier.T1,
    ActionKind.RANK_FINANCING: Tier.T1,
    ActionKind.RECORD_PAYMENT: Tier.T2,
    ActionKind.MARK_DISPUTED: Tier.T2,
    ActionKind.INITIATE_PAYMENT: Tier.T3,
    ActionKind.ACCEPT_FINANCING_OFFER: Tier.T3,
}
# Hard-coded, never configurable: external or irreversible.
ALWAYS_T3 = frozenset({ActionKind.INITIATE_PAYMENT, ActionKind.ACCEPT_FINANCING_OFFER})
ORDER = (Tier.T0, Tier.T1, Tier.T2, Tier.T3)
# Simulations move no money, so their amounts never escalate.
NO_ESCALATION = frozenset({ActionKind.RUN_SCENARIO})


def escalate(tier: Tier, amount: Decimal | None, ceilings: Mapping[Tier, Decimal]) -> Tier:
    """Move up one tier for each ceiling the amount exceeds, stopping at T3."""
    if amount is None:
        return tier
    while tier is not Tier.T3 and tier in ceilings and amount > ceilings[tier]:
        tier = ORDER[ORDER.index(tier) + 1]
    return tier


def at_least(tier: Tier, floor: Tier) -> Tier:
    return max(tier, floor, key=ORDER.index)


def assign_tier(
    kind: ActionKind, amount: Decimal | None, ceilings: Mapping[Tier, Decimal], needs_human: bool
) -> Tier:
    """``needs_human``: a rule result (e.g. a suspected duplicate) that must never auto-pass."""
    if kind in ALWAYS_T3:
        return Tier.T3
    tier = BASE_TIER[kind]
    if kind not in NO_ESCALATION:
        tier = escalate(tier, amount, ceilings)
    if needs_human:
        tier = at_least(tier, Tier.T2)
    return tier
