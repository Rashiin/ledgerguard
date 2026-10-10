"""13-week cash-flow projection, as pure functions over plain rows.

The model is kept simple enough for a business owner to follow:

* opening cash is the balance of the bank and cash accounts on the ledger
  date. Credit lines are headroom, not cash;
* each open receivable is expected on its due date plus that customer's
  mean lateness in the past;
* payables, payroll and obligations are expected on their due dates;
* anything already past due lands in week 1.

Every line carries a confidence class: CONTRACTED, EXPECTED or SPECULATIVE.
Classes are never merged. Each week reports inflow and outflow per class and
three running balances. Contracted-only is what an owner can rely on. The
all-classes balance is the full model.

Week 1 starts on the Monday after the as-of date.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal

from ledgerguard.domain.enums import Confidence, Direction
from ledgerguard.money import CENT

ZERO = Decimal("0.00")


@dataclass(frozen=True)
class PaidInvoice:
    counterparty_id: str
    due_on: date
    paid_on: date


@dataclass(frozen=True)
class ExpectedFlow:
    """One expected cash movement, traceable to the row it came from."""

    on: date
    amount: Decimal
    direction: Direction
    confidence: Confidence
    source_table: str
    source_id: str
    label: str


@dataclass(frozen=True)
class ProjectedWeek:
    index: int  # 1-based
    starts_on: date
    ends_on: date
    opening: Decimal
    inflow: dict[Confidence, Decimal]
    outflow: dict[Confidence, Decimal]
    closing_contracted: Decimal  # opening plus CONTRACTED lines only, cumulative
    closing_with_expected: Decimal  # CONTRACTED + EXPECTED, cumulative
    closing: Decimal  # all three classes, cumulative
    flows: tuple[ExpectedFlow, ...] = field(repr=False)

    @property
    def total_inflow(self) -> Decimal:
        return sum(self.inflow.values(), ZERO)

    @property
    def total_outflow(self) -> Decimal:
        return sum(self.outflow.values(), ZERO)


def mean_lateness_days(history: Iterable[PaidInvoice]) -> dict[str, int]:
    """Mean of (paid_on - due_on) in whole days per counterparty, rounded half up."""
    totals: dict[str, int] = defaultdict(int)
    counts: dict[str, int] = defaultdict(int)
    for item in history:
        totals[item.counterparty_id] += (item.paid_on - item.due_on).days
        counts[item.counterparty_id] += 1
    return {
        cp: int((Decimal(totals[cp]) / counts[cp]).to_integral_value(rounding=ROUND_HALF_UP))
        for cp in sorted(totals)
    }


def expected_receipt_date(due_on: date, lateness_days: int) -> date:
    return due_on + timedelta(days=lateness_days)


def first_week_start(as_of: date) -> date:
    """The Monday strictly after ``as_of``."""
    return as_of + timedelta(days=7 - as_of.weekday())


def horizon_end(as_of: date, weeks: int = 13) -> date:
    return first_week_start(as_of) + timedelta(days=7 * weeks - 1)


def _by_class(flows: Iterable[ExpectedFlow], direction: Direction) -> dict[Confidence, Decimal]:
    totals = {c: ZERO for c in Confidence}
    for f in flows:
        if f.direction is direction:
            totals[f.confidence] += f.amount
    return totals


def _net(
    flows: dict[Confidence, Decimal],
    out: dict[Confidence, Decimal],
    classes: tuple[Confidence, ...],
) -> Decimal:
    return sum((flows[c] - out[c] for c in classes), ZERO)


def project(
    as_of: date, opening: Decimal, flows: Sequence[ExpectedFlow], weeks: int = 13
) -> list[ProjectedWeek]:
    if weeks < 1:
        raise ValueError("weeks must be positive")
    start = first_week_start(as_of)
    buckets: list[list[ExpectedFlow]] = [[] for _ in range(weeks)]
    for flow in flows:
        index = max(0, (flow.on - start).days // 7)
        if index < weeks:
            buckets[index].append(flow)

    contracted = (Confidence.CONTRACTED,)
    with_expected = (Confidence.CONTRACTED, Confidence.EXPECTED)
    everything = tuple(Confidence)
    result: list[ProjectedWeek] = []
    opening = opening.quantize(CENT)
    c_bal, e_bal, all_bal = opening, opening, opening
    for i, bucket in enumerate(buckets):
        inflow = _by_class(bucket, Direction.IN)
        outflow = _by_class(bucket, Direction.OUT)
        week_start = start + timedelta(days=7 * i)
        opening_all = all_bal
        c_bal += _net(inflow, outflow, contracted)
        e_bal += _net(inflow, outflow, with_expected)
        all_bal += _net(inflow, outflow, everything)
        result.append(
            ProjectedWeek(
                index=i + 1,
                starts_on=week_start,
                ends_on=week_start + timedelta(days=6),
                opening=opening_all,
                inflow=inflow,
                outflow=outflow,
                closing_contracted=c_bal,
                closing_with_expected=e_bal,
                closing=all_bal,
                flows=tuple(sorted(bucket, key=lambda f: (f.on, f.source_id))),
            )
        )
    return result
