"""Which action kinds and tools a turn may use, decided from the owner's request alone.

The plan is derived before any ledger content is read, from the words the
owner typed. Text inside invoices, names or documents never reaches this
function, so injected content cannot widen what a turn is allowed to do. The
schema handed to the model runtime is built from the allowed kinds, so the
model cannot even express an action outside the plan.

Routing is a short list of plain patterns. It is meant to be read and tested,
not to understand language. A request that matches nothing may only answer
questions.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from ledgerguard.domain.enums import ActionKind
from ledgerguard.facts import CASH_PROJECTION, OPEN_INVOICES, REVENUE_CONCENTRATION


@dataclass(frozen=True)
class TurnPlan:
    kinds: frozenset[ActionKind]
    tools: tuple[str, ...]
    matched: tuple[str, ...] = field(default=())  # names of the routes that fired


@dataclass(frozen=True)
class Route:
    name: str
    pattern: re.Pattern[str]
    kinds: frozenset[ActionKind]
    tools: tuple[str, ...]


def _route(name: str, pattern: str, kinds: set[ActionKind], tools: tuple[str, ...]) -> Route:
    return Route(name, re.compile(pattern, re.IGNORECASE), frozenset(kinds), tools)


K = ActionKind
ROUTES = (
    _route(
        "cash",
        r"\b(cash|quarter|forecast|projection|week \d+|runway|short(fall)?)\b",
        {K.DRAFT_FORECAST},
        (CASH_PROJECTION,),
    ),
    _route(
        "scenario",
        r"\bwhat if\b|\bsimulat|\bscenario\b",
        {K.RUN_SCENARIO},
        (CASH_PROJECTION, REVENUE_CONCENTRATION),
    ),
    _route(
        "financing",
        r"\b(financ\w*|loan|factoring|credit line|options|borrow)\b",
        {K.RANK_FINANCING},
        (CASH_PROJECTION,),
    ),
    _route(
        "accept_offer",
        r"\baccept\b.*\b(offer|factoring|loan|financing|credit)\b",
        {K.ACCEPT_FINANCING_OFFER},
        (CASH_PROJECTION,),
    ),
    _route(
        "record_payment",
        r"\b(record|log|mark)\b.*\b(paid|payment|received)\b|"
        r"\b(paid|has paid|have paid)\b.*\binvoice\b",
        {K.RECORD_PAYMENT},
        (OPEN_INVOICES,),
    ),
    _route("pay", r"\bpay\b(?!ments? received)|\bsettle\b", {K.INITIATE_PAYMENT}, (OPEN_INVOICES,)),
    _route("dispute", r"\bdisput", {K.MARK_DISPUTED}, (OPEN_INVOICES,)),
    _route(
        "invoice_draft",
        r"\b(draft|raise|create|issue)\b.*\binvoice\b",
        {K.DRAFT_INVOICE},
        (REVENUE_CONCENTRATION,),
    ),
    _route("anomaly", r"\b(unusual|anomal\w*|odd|strange|suspicious)\b", {K.FLAG_ANOMALY}, ()),
    _route(
        "revenue",
        r"\b(revenue|customers?|concentration|biggest client|sales)\b",
        set(),
        (REVENUE_CONCENTRATION,),
    ),
)


def plan_turn(request: str) -> TurnPlan:
    kinds: set[ActionKind] = {K.ANSWER_QUERY}
    tools: list[str] = []
    matched = []
    for route in ROUTES:
        if route.pattern.search(request):
            matched.append(route.name)
            kinds |= route.kinds
            tools += [t for t in route.tools if t not in tools]
    return TurnPlan(frozenset(kinds), tuple(tools), tuple(matched))
