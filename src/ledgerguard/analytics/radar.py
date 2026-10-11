"""Cash-Flow Radar: alerts and what-if scenarios over the 13-week projection.

Everything here is a pure function of the ledger snapshot. An alert states its
severity, how many days of warning it gives, the rows behind it and the
confidence classes it depends on. A shortfall that appears on contracted
lines alone is a different message from one that needs the order book to
fail, and the alert says which it is.

A scenario changes the projected flows, projects again and compares the
alerts before and after. Nothing is written.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field, replace
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from ledgerguard.analytics.cashflow import ExpectedFlow, ProjectedWeek, project
from ledgerguard.domain.enums import (
    AccountType,
    Confidence,
    Direction,
    InvoiceStatus,
    InvoiceType,
    ObligationKind,
    PayrollStatus,
)
from ledgerguard.money import CENT, format_eur
from ledgerguard.untrusted import neutralise
from ledgerguard.verifier.snapshot import Band, LedgerSnapshot

ZERO = Decimal("0.00")
SEVERITIES = ("low", "medium", "high", "critical")
OVERDUE_HIGH_DAYS = 60
OVERDUE_MEDIUM_DAYS = 14


@dataclass(frozen=True)
class Alert:
    id: str
    kind: str  # SHORTFALL, PAYROLL_PRESSURE, TAX_RESERVE, OVERDUE, DISPUTED, CONCENTRATION
    severity: str
    headline: str  # written by code; shown verbatim when the alert is mandatory
    lead_days: int
    rows: tuple[str, ...]
    classes: tuple[str, ...]  # the confidence classes the alert depends on

    @property
    def high(self) -> bool:
        return SEVERITIES.index(self.severity) >= SEVERITIES.index("high")

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "kind": self.kind,
            "severity": self.severity,
            "headline": self.headline,
            "lead_days": self.lead_days,
            "rows": list(self.rows),
            "classes": list(self.classes),
        }


@dataclass(frozen=True)
class Projection:
    """A projection together with the flows it was built from."""

    cash: Decimal
    flows: tuple[ExpectedFlow, ...]
    weeks: tuple[ProjectedWeek, ...]

    def cash_on(self, day: date, band: Sequence[Confidence]) -> Decimal:
        total = self.cash
        for f in self.flows:
            if f.confidence in band and f.on <= day:
                total += f.amount if f.direction is Direction.IN else -f.amount
        return total


def baseline(snap: LedgerSnapshot) -> Projection:
    return Projection(snap.cash_on_hand(), tuple(snap.flows), tuple(snap.weeks))


def reproject(snap: LedgerSnapshot, flows: Iterable[ExpectedFlow]) -> Projection:
    flows = tuple(flows)
    cash = snap.cash_on_hand()
    return Projection(cash, flows, tuple(project(snap.as_of, cash, list(flows), len(snap.weeks))))


def _classes(band: Sequence[Confidence]) -> tuple[str, ...]:
    return tuple(c.value for c in band)


# --------------------------------------------------------------------------- alerts


def compute_alerts(
    snap: LedgerSnapshot,
    proj: Projection,
    band_name: str,
    payroll_margin: Decimal,
    concentration: Decimal,
) -> list[Alert]:
    band = Band.BY_NAME[band_name]
    alerts: list[Alert] = []
    alerts += _shortfall(snap, proj)
    alerts += _payroll(snap, proj, band, payroll_margin)
    alerts += _tax(snap, proj, band)
    alerts += _receivables(snap)
    alerts += _concentration(snap, concentration)
    return sorted(alerts, key=lambda a: (-SEVERITIES.index(a.severity), a.lead_days, a.id))


def _shortfall(snap: LedgerSnapshot, proj: Projection) -> list[Alert]:
    negative = [w for w in proj.weeks if w.closing < 0]
    if not negative:
        return []
    worst = min(negative, key=lambda w: (w.closing, w.index))
    first = negative[0]
    contracted = [w for w in proj.weeks if w.closing_contracted < 0]
    on_contracted = bool(contracted) and contracted[0].index <= first.index
    headline = (
        f"Projected cash falls below zero from week {first.index}, starting {first.starts_on}. "
        f"The projected low is {format_eur(worst.closing)} in week {worst.index}."
    )
    headline += (
        " It appears on contracted lines alone, before counting any order not yet invoiced."
        if on_contracted
        else " It depends on lines that are not yet contracted."
    )
    rows = tuple(dict.fromkeys(f.source_id for f in worst.flows))[:6]
    return [
        Alert(
            id="shortfall",
            kind="SHORTFALL",
            severity="high",
            headline=headline,
            lead_days=(first.starts_on - snap.as_of).days,
            rows=rows,
            classes=("CONTRACTED",) if on_contracted else _classes(tuple(Confidence)),
        )
    ]


def _payroll(
    snap: LedgerSnapshot, proj: Projection, band: Sequence[Confidence], margin: Decimal
) -> list[Alert]:
    alerts = []
    end = proj.weeks[-1].ends_on
    for run in sorted(snap.payroll_runs.values(), key=lambda r: r.pay_date):
        if run.status is not PayrollStatus.SCHEDULED or run.pay_date > end:
            continue
        need = run.employer_cost_total
        cash = proj.cash_on(run.pay_date - timedelta(days=1), band)
        if cash >= need * (1 + margin):
            continue
        short = cash < need
        headline = (
            f"Payroll {run.id} on {run.pay_date} needs {format_eur(need)}. Projected cash the "
            f"day before is {format_eur(cash)}"
            + (
                f", short by {format_eur(need - cash)}."
                if short
                else f", leaving only {format_eur(cash - need)} to spare."
            )
        )
        alerts.append(
            Alert(
                id=f"payroll.{run.id}",
                kind="PAYROLL_PRESSURE",
                severity="high" if short else "medium",
                headline=headline,
                lead_days=(run.pay_date - snap.as_of).days,
                rows=(run.id,),
                classes=_classes(band),
            )
        )
    return alerts


def _tax(snap: LedgerSnapshot, proj: Projection, band: Sequence[Confidence]) -> list[Alert]:
    alerts = []
    end = proj.weeks[-1].ends_on
    for ob in sorted(snap.obligations.values(), key=lambda o: o.due_on):
        if ob.kind is not ObligationKind.TAX or ob.due_on > end:
            continue
        cash = proj.cash_on(ob.due_on - timedelta(days=1), band)
        if cash >= ob.amount:
            continue
        label, _ = neutralise(ob.description)
        alerts.append(
            Alert(
                id=f"tax.{ob.id}",
                kind="TAX_RESERVE",
                severity="high",
                headline=(
                    f"{label}: {format_eur(ob.amount)} is due on {ob.due_on}. Projected cash "
                    f"the day before is {format_eur(cash)}."
                ),
                lead_days=(ob.due_on - snap.as_of).days,
                rows=(ob.id,),
                classes=_classes(band),
            )
        )
    return alerts


def _receivables(snap: LedgerSnapshot) -> list[Alert]:
    alerts = []
    for inv in sorted(snap.invoices.values(), key=lambda i: i.id):
        if inv.type is not InvoiceType.RECEIVABLE:
            continue
        name, _ = neutralise(snap.counterparties[inv.counterparty_id].name)
        owed = inv.gross_amount - inv.paid_amount
        if inv.status is InvoiceStatus.OVERDUE:
            late = (snap.as_of - inv.due_on).days
            severity = (
                "high"
                if late > OVERDUE_HIGH_DAYS
                else "medium"
                if late > OVERDUE_MEDIUM_DAYS
                else "low"
            )
            alerts.append(
                Alert(
                    id=f"overdue.{inv.id}",
                    kind="OVERDUE",
                    severity=severity,
                    headline=(
                        f"{name} owes {format_eur(owed)} on invoice {inv.id}, {late} days past due."
                    ),
                    lead_days=0,
                    rows=(inv.id, inv.counterparty_id),
                    classes=("CONTRACTED",),
                )
            )
        elif inv.status is InvoiceStatus.DISPUTED:
            alerts.append(
                Alert(
                    id=f"disputed.{inv.id}",
                    kind="DISPUTED",
                    severity="medium",
                    headline=(
                        f"{name} disputes invoice {inv.id} for {format_eur(owed)}. It is left "
                        "out of expected receipts until resolved."
                    ),
                    lead_days=0,
                    rows=(inv.id, inv.counterparty_id),
                    classes=("CONTRACTED",),
                )
            )
    return alerts


def _concentration(snap: LedgerSnapshot, threshold: Decimal) -> list[Alert]:
    alerts = []
    for cp, share in sorted(snap.revenue_shares().items()):
        if share <= threshold:
            continue
        name, _ = neutralise(snap.counterparties[cp].name)
        percent = (share * 100).quantize(Decimal("0.1"), rounding=ROUND_HALF_UP)
        alerts.append(
            Alert(
                id=f"concentration.{cp}",
                kind="CONCENTRATION",
                severity="high" if share > Decimal("0.5") else "medium",
                headline=(
                    f"{name} is {percent}% of net revenue over the last 12 months. A delay "
                    "from this one customer moves the whole forecast."
                ),
                lead_days=0,
                rows=(cp,),
                classes=("CONTRACTED",),
            )
        )
    return alerts


# --------------------------------------------------------------------------- scenarios


@dataclass(frozen=True)
class ScenarioResult:
    description: str
    before: Projection
    after: Projection
    alerts_before: list[Alert]
    alerts_after: list[Alert]
    rows: tuple[str, ...] = field(default=())

    @property
    def new_alerts(self) -> list[Alert]:
        before = {a.id for a in self.alerts_before}
        return [a for a in self.alerts_after if a.id not in before]

    @property
    def cleared_alerts(self) -> list[Alert]:
        after = {a.id for a in self.alerts_after}
        return [a for a in self.alerts_before if a.id not in after]

    def lowest(self, which: str = "after") -> ProjectedWeek:
        weeks = (self.after if which == "after" else self.before).weeks
        return min(weeks, key=lambda w: (w.closing, w.index))

    def as_dict(self) -> dict[str, Any]:
        def weeks(p: Projection) -> list[dict[str, str]]:
            return [
                {
                    "week": str(w.index),
                    "closing": str(w.closing),
                    "closing_contracted": str(w.closing_contracted),
                }
                for w in p.weeks
            ]

        return {
            "description": self.description,
            "before": weeks(self.before),
            "after": weeks(self.after),
            "new_alerts": [a.as_dict() for a in self.new_alerts],
            "cleared_alerts": [a.as_dict() for a in self.cleared_alerts],
        }


def canonical_scenario(scenario: dict[str, Any]) -> str:
    """One stable text form of a scenario, so it can travel in derivation params."""
    return json.dumps(scenario, sort_keys=True, separators=(",", ":"))


def simulate(
    snap: LedgerSnapshot,
    scenario: dict[str, Any],
    band_name: str,
    payroll_margin: Decimal,
    concentration: Decimal,
) -> ScenarioResult:
    flows, description, rows = apply_scenario(snap, scenario)
    before = baseline(snap)
    after = reproject(snap, flows)
    alerts = [
        compute_alerts(snap, p, band_name, payroll_margin, concentration) for p in (before, after)
    ]
    return ScenarioResult(description, before, after, alerts[0], alerts[1], rows)


def apply_scenario(
    snap: LedgerSnapshot, scenario: dict[str, Any]
) -> tuple[list[ExpectedFlow], str, tuple[str, ...]]:
    kind = scenario["type"]
    flows = list(snap.flows)
    if kind == "customer_pays_late":
        cp, days = scenario["counterparty_id"], int(scenario["days"])
        if cp not in snap.counterparties:
            raise ValueError(f"unknown counterparty {cp}")
        name, _ = neutralise(snap.counterparties[cp].name)
        moved = []
        for i, f in enumerate(flows):
            inv = snap.invoices.get(f.source_id) if f.source_table == "invoice" else None
            if (
                f.direction is Direction.IN
                and inv is not None
                and inv.counterparty_id == cp
                and f.confidence is not Confidence.SPECULATIVE
            ):
                flows[i] = replace(f, on=f.on + timedelta(days=days))
                moved.append(f.source_id)
        return flows, f"{name} paying {days} days later than usual", (cp, *moved)
    if kind == "delay_payable":
        invoice_id, days = scenario["invoice_id"], int(scenario["days"])
        target = snap.invoices.get(invoice_id)
        if target is None or target.type is not InvoiceType.PAYABLE:
            raise ValueError(f"{invoice_id} is not a supplier invoice")
        flows = [
            replace(f, on=f.on + timedelta(days=days)) if f.source_id == invoice_id else f
            for f in flows
        ]
        supplier, _ = neutralise(snap.counterparties[target.counterparty_id].name)
        return (
            flows,
            f"{supplier} invoice {invoice_id} paid {days} days later",
            (invoice_id, target.counterparty_id),
        )
    if kind == "draw_credit_line":
        amount = Decimal(str(scenario["amount"])).quantize(CENT)
        on = date.fromisoformat(scenario["on"])
        account, rate = _credit_facility(snap)
        flows.append(
            ExpectedFlow(
                on,
                amount,
                Direction.IN,
                Confidence.CONTRACTED,
                "account",
                account,
                f"Draw on the credit facility {account}",
            )
        )
        for month_end, interest in _monthly_interest(amount, rate, on, snap.weeks[-1].ends_on):
            flows.append(
                ExpectedFlow(
                    month_end,
                    interest,
                    Direction.OUT,
                    Confidence.CONTRACTED,
                    "account",
                    account,
                    "Interest on the credit facility",
                )
            )
        return flows, f"drawing {format_eur(amount)} on the credit facility on {on}", (account,)
    if kind == "contracted_inflows_only":
        flows = [
            f
            for f in flows
            if f.direction is Direction.OUT or f.confidence is Confidence.CONTRACTED
        ]
        return flows, "only contracted receipts arriving, against every expected payment", ()
    raise ValueError(f"unknown scenario type {kind!r}")


def _credit_facility(snap: LedgerSnapshot) -> tuple[str, Decimal]:
    account = next(
        a
        for a in sorted(snap.accounts.values(), key=lambda a: a.id)
        if a.type is AccountType.CREDIT_LINE
    )
    for product in snap.financing_products.values():
        if product.eligibility_rules.get("existing_facility_account") == account.id:
            return account.id, product.nominal_rate
    return account.id, Decimal("0")


def _monthly_interest(
    amount: Decimal, rate: Decimal, start: date, until: date
) -> list[tuple[date, Decimal]]:
    """Simple interest charged at each month end while the draw is outstanding."""
    out = []
    cursor = start
    while cursor <= until:
        nxt = date(cursor.year + (cursor.month == 12), cursor.month % 12 + 1, 1)
        month_end = min(nxt - timedelta(days=1), until)
        days = (month_end - cursor).days + 1
        interest = (amount * rate * days / 365).quantize(CENT, rounding=ROUND_HALF_UP)
        if interest > 0:
            out.append((month_end, interest))
        cursor = nxt
    return out
