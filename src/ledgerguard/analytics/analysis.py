"""Financial analysis: a handful of ratios and a deliberately simple anomaly check.

Every metric is computed from ledger rows with a stated method, so an owner or
an accountant can redo it by hand. Where the ledger lacks data, the method
says what was assumed. There is no stock ledger, so the cash conversion cycle
uses zero days of inventory.

Anomalies come from three plain checks: a median/MAD z-score within each
category and counterparty, supplier invoices that look like duplicates, and
manual bookings made on a weekend.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal
from itertools import pairwise

from ledgerguard.db.models import Transaction
from ledgerguard.domain.enums import (
    Direction,
    InvoiceStatus,
    InvoiceType,
    TransactionSource,
)
from ledgerguard.money import CENT, format_eur
from ledgerguard.untrusted import neutralise
from ledgerguard.verifier.snapshot import LedgerSnapshot

ZERO = Decimal("0.00")
YEAR = 365
# Operating costs paid in cash that have no supplier invoice behind them.
OPEX_CATEGORIES = frozenset(
    {
        "payroll",
        "lease",
        "utilities",
        "fuel",
        "insurance",
        "software",
        "bank_fees",
    }
)
MAD_THRESHOLD = Decimal(5)
MIN_GROUP = 12
DUPLICATE_WINDOW_DAYS = 14


@dataclass(frozen=True)
class Metric:
    name: str
    value: Decimal
    unit: str  # money, percent or days
    method: str  # how it is computed, in plain words


def _window(snap: LedgerSnapshot) -> tuple[date, date]:
    return snap.as_of - timedelta(days=YEAR), snap.as_of


def _invoiced(snap: LedgerSnapshot, side: InvoiceType, gross: bool) -> Decimal:
    start, end = _window(snap)
    return sum(
        (
            (i.gross_amount if gross else i.net_amount)
            for i in snap.invoices.values()
            if i.type is side and start < i.issued_on <= end
        ),
        ZERO,
    )


def _open(snap: LedgerSnapshot, side: InvoiceType) -> Decimal:
    return sum(
        (
            i.gross_amount - i.paid_amount
            for i in snap.invoices.values()
            if i.type is side
            and i.status
            in (
                InvoiceStatus.SENT,
                InvoiceStatus.OVERDUE,
                InvoiceStatus.PART_PAID,
                InvoiceStatus.DISPUTED,
            )
        ),
        ZERO,
    )


def _opex(snap: LedgerSnapshot) -> Decimal:
    start, end = _window(snap)
    return sum(
        (
            t.amount
            for t in snap.transactions.values()
            if t.direction is Direction.OUT
            and t.category in OPEX_CATEGORIES
            and start < t.booked_on <= end
        ),
        ZERO,
    )


def _monthly_net(snap: LedgerSnapshot, months: int = 3) -> Decimal:
    """Average monthly operating cash movement over the last full months.

    Financing and owner flows are left out: loan drawdowns, dividends and
    equipment purchases are decisions, not the running cost of the business.
    """
    first_of_month = snap.as_of.replace(day=1)
    start = first_of_month
    for _ in range(months):
        start = (start - timedelta(days=1)).replace(day=1)
    excluded = {"loan_drawdown", "dividend", "capex"}
    total = ZERO
    for t in snap.transactions.values():
        if start <= t.booked_on < first_of_month and t.category not in excluded:
            total += t.amount if t.direction is Direction.IN else -t.amount
    return (total / months).quantize(CENT, rounding=ROUND_HALF_UP)


def _pct(part: Decimal, whole: Decimal) -> Decimal:
    if whole == 0:
        return ZERO
    return (part * 100 / whole).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def _days(part: Decimal, whole: Decimal) -> Decimal:
    if whole == 0:
        return ZERO
    return (part * YEAR / whole).quantize(Decimal("0.1"), rounding=ROUND_HALF_UP)


def metrics(snap: LedgerSnapshot) -> dict[str, Metric]:
    revenue = _invoiced(snap, InvoiceType.RECEIVABLE, gross=False)
    cogs = _invoiced(snap, InvoiceType.PAYABLE, gross=False)
    opex = _opex(snap)
    net_flow = _monthly_net(snap)
    dso = _days(_open(snap, InvoiceType.RECEIVABLE), _invoiced(snap, InvoiceType.RECEIVABLE, True))
    dpo = _days(_open(snap, InvoiceType.PAYABLE), _invoiced(snap, InvoiceType.PAYABLE, True))
    burn = max(-net_flow, ZERO)
    runway = (
        (snap.cash_on_hand() / burn * Decimal(30)).quantize(Decimal("0.1"), rounding=ROUND_HALF_UP)
        if burn > 0
        else ZERO
    )
    found = [
        Metric(
            "revenue_12m",
            revenue,
            "money",
            "Net value of sales invoices issued in the last 12 months.",
        ),
        Metric(
            "cost_of_goods_12m",
            cogs,
            "money",
            "Net value of supplier invoices issued in the last 12 months. Supplier "
            "invoices are stock purchases for this business.",
        ),
        Metric(
            "operating_costs_12m",
            opex,
            "money",
            "Payroll, rent, utilities, fuel, insurance, software and bank fees paid in the "
            "last 12 months.",
        ),
        Metric(
            "gross_margin",
            _pct(revenue - cogs, revenue),
            "percent",
            "Revenue less cost of goods, as a share of revenue, over 12 months.",
        ),
        Metric(
            "net_margin",
            _pct(revenue - cogs - opex, revenue),
            "percent",
            "Revenue less cost of goods and operating costs, as a share of revenue, over "
            "12 months. Tax, interest and owner payments are left out.",
        ),
        Metric(
            "monthly_operating_cash_flow",
            net_flow,
            "money",
            "Average monthly operating cash in less cash out over the last three full "
            "months, leaving out loans, dividends and equipment purchases.",
        ),
        Metric(
            "burn_rate",
            burn,
            "money",
            "Monthly operating cash outflow net of inflow, zero when the business generates cash.",
        ),
        Metric(
            "runway_days",
            runway,
            "days",
            "Days the current cash would last at the burn rate, zero when there is no burn.",
        ),
        Metric(
            "dso",
            dso,
            "days",
            "Open receivables divided by gross invoicing over 12 months, times 365.",
        ),
        Metric(
            "dpo",
            dpo,
            "days",
            "Open payables divided by gross supplier invoicing over 12 months, times 365.",
        ),
        Metric(
            "cash_conversion_cycle",
            (dso - dpo).quantize(Decimal("0.1")),
            "days",
            "DSO less DPO. There is no stock ledger, so inventory days are taken as zero.",
        ),
    ]
    return {m.name: m for m in found}


UNITS: dict[str, str] = {
    "revenue_12m": "money",
    "cost_of_goods_12m": "money",
    "operating_costs_12m": "money",
    "gross_margin": "percent",
    "net_margin": "percent",
    "monthly_operating_cash_flow": "money",
    "burn_rate": "money",
    "runway_days": "days",
    "dso": "days",
    "dpo": "days",
    "cash_conversion_cycle": "days",
    "financing_gap": "money",
}


# --------------------------------------------------------------------------- anomalies


@dataclass(frozen=True)
class Anomaly:
    id: str
    kind: str  # OUTLIER, DUPLICATE or OFF_HOURS
    rows: tuple[str, ...]
    statement: str
    amount: Decimal
    booked_on: date


def _median(values: list[Decimal]) -> Decimal:
    ordered = sorted(values)
    n = len(ordered)
    return (ordered[(n - 1) // 2] + ordered[n // 2]) / 2


def mad_z(values: list[Decimal], x: Decimal) -> Decimal | None:
    """(x - median) / (1.4826 * MAD). None when the spread is zero."""
    med = _median(values)
    mad = _median([abs(v - med) for v in values])
    if mad == 0:
        return None
    return (x - med) / (Decimal("1.4826") * mad)


def anomalies(snap: LedgerSnapshot) -> list[Anomaly]:
    found: list[Anomaly] = []
    groups: dict[tuple[str, str, str], list[Transaction]] = defaultdict(list)
    for t in snap.transactions.values():
        groups[(t.direction.value, t.category, t.counterparty_id)].append(t)
    for txns in groups.values():
        amounts = [t.amount for t in txns]
        if len(txns) < MIN_GROUP or len(set(amounts)) < len(amounts) // 2:
            continue
        med = _median(amounts)
        for t in txns:
            z = mad_z(amounts, t.amount)
            if z is not None and abs(z) > MAD_THRESHOLD:
                name, _ = neutralise(snap.counterparties[t.counterparty_id].name)
                found.append(
                    Anomaly(
                        id=f"anom.{t.id}",
                        kind="OUTLIER",
                        rows=(t.id,),
                        amount=t.amount,
                        booked_on=t.booked_on,
                        statement=(
                            f"{name}: {format_eur(t.amount)} on {t.booked_on} is "
                            f"{abs(z).quantize(Decimal('0.1'))} median absolute deviations "
                            f"{'above' if z > 0 else 'below'} the usual {t.category} amount, "
                            f"whose median is {format_eur(med.quantize(CENT))}."
                        ),
                    )
                )
    payables = sorted(
        (i for i in snap.invoices.values() if i.type is InvoiceType.PAYABLE),
        key=lambda i: (i.counterparty_id, i.issued_on, i.id),
    )
    for a, b in pairwise(payables):
        if (
            a.counterparty_id == b.counterparty_id
            and a.gross_amount == b.gross_amount
            and (b.issued_on - a.issued_on).days <= DUPLICATE_WINDOW_DAYS
        ):
            name, _ = neutralise(snap.counterparties[a.counterparty_id].name)
            found.append(
                Anomaly(
                    id=f"anom.dup.{a.id}.{b.id}",
                    kind="DUPLICATE",
                    rows=(a.id, b.id),
                    amount=b.gross_amount,
                    booked_on=b.issued_on,
                    statement=(
                        f"{name} invoices {a.id} and {b.id} are both "
                        f"{format_eur(b.gross_amount)}, issued "
                        f"{(b.issued_on - a.issued_on).days} days apart. {a.id} is "
                        f"{a.status.value} and {b.id} is {b.status.value}."
                    ),
                )
            )
    for t in snap.transactions.values():
        if t.source is TransactionSource.MANUAL and t.booked_on.weekday() >= 5:
            found.append(
                Anomaly(
                    id=f"anom.weekend.{t.id}",
                    kind="OFF_HOURS",
                    rows=(t.id,),
                    amount=t.amount,
                    booked_on=t.booked_on,
                    statement=f"A manual booking of {format_eur(t.amount)} was made on a weekend, "
                    f"{t.booked_on}.",
                )
            )
    return sorted(found, key=lambda a: (a.booked_on, a.id), reverse=True)
