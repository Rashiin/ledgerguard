"""13-week cash-flow projection, as pure functions over plain rows.

The model is deliberately simple enough for a business owner to follow:

* opening = cash on bank and cash accounts at the ledger's as-of date
  (credit lines are financing headroom, not cash);
* each open receivable is expected on its due date plus that customer's
  historical mean lateness;
* payables, payroll and obligations are expected on their due dates;
* anything already past due lands in week 1.

Week 1 starts on the Monday after the as-of date.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal

from ledgerguard.domain.enums import Direction
from ledgerguard.money import CENT


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
    source_table: str
    source_id: str
    label: str


@dataclass(frozen=True)
class ProjectedWeek:
    index: int  # 1-based
    starts_on: date
    ends_on: date
    opening: Decimal
    inflow: Decimal
    outflow: Decimal
    closing: Decimal
    flows: tuple[ExpectedFlow, ...] = field(repr=False)


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

    result: list[ProjectedWeek] = []
    balance = opening.quantize(CENT)
    for i, bucket in enumerate(buckets):
        inflow = sum((f.amount for f in bucket if f.direction is Direction.IN), Decimal("0.00"))
        outflow = sum((f.amount for f in bucket if f.direction is Direction.OUT), Decimal("0.00"))
        closing = balance + inflow - outflow
        week_start = start + timedelta(days=7 * i)
        result.append(
            ProjectedWeek(
                index=i + 1,
                starts_on=week_start,
                ends_on=week_start + timedelta(days=6),
                opening=balance,
                inflow=inflow,
                outflow=outflow,
                closing=closing,
                flows=tuple(sorted(bucket, key=lambda f: (f.on, f.source_id))),
            )
        )
        balance = closing
    return result
