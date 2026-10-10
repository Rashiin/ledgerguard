"""Stage 6: permission tiers.

The tier is a fixed function of the action kind. The model never chooses
it, and a tier in the model's output is discarded at parse time.

Amount ceilings do not move tiers. The ceiling limits money leaving the
business, and the only kinds that move money out are always T3 already. An
outbound amount above the ceiling is blocked by the AmountCeiling rule.
"""

from __future__ import annotations

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
# External or irreversible. Hard-coded so that no configuration can lower them.
ALWAYS_T3 = frozenset({ActionKind.INITIATE_PAYMENT, ActionKind.ACCEPT_FINANCING_OFFER})
# The kinds that commit money leaving the business. The outbound ceiling applies to these.
OUTBOUND = ALWAYS_T3
ORDER = (Tier.T0, Tier.T1, Tier.T2, Tier.T3)


def at_least(tier: Tier, floor: Tier) -> Tier:
    return max(tier, floor, key=ORDER.index)


def assign_tier(kind: ActionKind, needs_human: bool) -> Tier:
    """``needs_human`` is set when a rule result, such as a suspected duplicate,
    must never pass without a person."""
    if kind in ALWAYS_T3:
        return Tier.T3
    tier = BASE_TIER[kind]
    return at_least(tier, Tier.T2) if needs_human else tier
