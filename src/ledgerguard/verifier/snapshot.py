"""A read-only, in-memory view of the ledger for one verification.

Loaded once from the read-only session, so every stage and every rule sees
the same state, and rules stay pure functions of (intent, snapshot).
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal
from typing import Any, ClassVar

from sqlalchemy import select
from sqlalchemy.orm import Session

from ledgerguard.analytics.cashflow import ExpectedFlow, ProjectedWeek, project
from ledgerguard.analytics.sources import projection_flows
from ledgerguard.db.models import (
    Account,
    Base,
    Business,
    Counterparty,
    FinancingProduct,
    Invoice,
    Obligation,
    PayrollRun,
    Transaction,
)
from ledgerguard.domain.enums import (
    AccountType,
    Confidence,
    Direction,
    InvoiceType,
    PayrollStatus,
)

ZERO = Decimal("0.00")
WEEKS = 13

# Row id prefix -> table. Ids are globally unique because each table has its own prefix.
PREFIXES = {
    "biz": "business",
    "acc": "account",
    "txn": "transaction",
    "cp": "counterparty",
    "inv": "invoice",
    "pay": "payroll_run",
    "obl": "obligation",
    "fin": "financing_product",
}


def table_of(row_id: str) -> str | None:
    return PREFIXES.get(row_id.split("_", 1)[0])


class Band:
    """Which confidence classes a balance counts (DECISIONS D-009)."""

    CONTRACTED = (Confidence.CONTRACTED,)
    WITH_EXPECTED = (Confidence.CONTRACTED, Confidence.EXPECTED)
    ALL = tuple(Confidence)

    BY_NAME: ClassVar[dict[str, tuple[Confidence, ...]]] = {
        "contracted": CONTRACTED,
        "with_expected": WITH_EXPECTED,
        "all": ALL,
    }


@dataclass
class LedgerSnapshot:
    business: Business
    accounts: dict[str, Account]
    balances: dict[str, Decimal]  # at data_as_of
    counterparties: dict[str, Counterparty]
    invoices: dict[str, Invoice]
    transactions: dict[str, Transaction]
    payroll_runs: dict[str, PayrollRun]
    obligations: dict[str, Obligation]
    financing_products: dict[str, FinancingProduct]
    flows: list[ExpectedFlow]
    weeks: list[ProjectedWeek]
    _tables: dict[str, dict[str, Any]] = field(default_factory=dict, repr=False)

    def __post_init__(self) -> None:
        self._tables = {
            "business": {self.business.id: self.business},
            "account": self.accounts,
            "transaction": self.transactions,
            "counterparty": self.counterparties,
            "invoice": self.invoices,
            "payroll_run": self.payroll_runs,
            "obligation": self.obligations,
            "financing_product": self.financing_products,
        }

    @property
    def as_of(self) -> date:
        return self.business.data_as_of

    def row(self, row_id: str) -> Base | None:
        table = table_of(row_id)
        if table is None:
            return None
        found: Base | None = self._tables[table].get(row_id)
        return found

    def cash_on_hand(self) -> Decimal:
        return sum(
            (
                self.balances[a.id]
                for a in self.accounts.values()
                if a.type is not AccountType.CREDIT_LINE
            ),
            ZERO,
        )

    def projected_cash_on(
        self,
        day: date,
        band: tuple[Confidence, ...],
        extra: list[ExpectedFlow] | None = None,
        removed: frozenset[str] = frozenset(),
    ) -> Decimal:
        """Cash at the end of ``day``: on hand now plus every banded flow up to that day.

        ``extra`` adds the flows an action would create; ``removed`` drops the
        projected flows it replaces (paying an invoice now removes its due-date line).
        """
        total = self.cash_on_hand()
        for f in [*(f for f in self.flows if f.source_id not in removed), *(extra or [])]:
            if f.confidence in band and f.on <= day:
                total += f.amount if f.direction is Direction.IN else -f.amount
        return total

    def next_payroll_after(self, day: date) -> PayrollRun | None:
        runs = sorted(
            (
                r
                for r in self.payroll_runs.values()
                if r.status is PayrollStatus.SCHEDULED and r.pay_date > day
            ),
            key=lambda r: r.pay_date,
        )
        return runs[0] if runs else None

    def revenue_shares(self) -> dict[str, Decimal]:
        """Each customer's share of trailing-12-month net invoiced revenue, as a fraction."""
        since = self.as_of - timedelta(days=365)
        revenue: dict[str, Decimal] = defaultdict(Decimal)
        for inv in self.invoices.values():
            if inv.type is InvoiceType.RECEIVABLE and since < inv.issued_on <= self.as_of:
                revenue[inv.counterparty_id] += inv.net_amount
        total = sum(revenue.values(), ZERO)
        return {cp: amount / total for cp, amount in revenue.items()} if total else {}


def _by_id(rows: Any) -> dict[str, Any]:
    return {r.id: r for r in rows}


def load_snapshot(session: Session) -> LedgerSnapshot:
    business = session.scalars(select(Business)).one()
    accounts = _by_id(session.scalars(select(Account)))
    transactions = _by_id(session.scalars(select(Transaction)))
    invoices = _by_id(session.scalars(select(Invoice)))
    payroll = _by_id(session.scalars(select(PayrollRun)))
    obligations = _by_id(session.scalars(select(Obligation)))
    balances = {a: acc.opening_balance for a, acc in accounts.items()}
    for t in transactions.values():
        if t.booked_on <= business.data_as_of:
            balances[t.account_id] += t.amount if t.direction is Direction.IN else -t.amount
    flows = projection_flows(
        business.data_as_of,
        WEEKS,
        invoices.values(),
        transactions.values(),
        payroll.values(),
        obligations.values(),
    )
    cash = sum(
        (balances[a] for a, acc in accounts.items() if acc.type is not AccountType.CREDIT_LINE),
        ZERO,
    )
    snapshot = LedgerSnapshot(
        business=business,
        accounts=accounts,
        balances=balances,
        counterparties=_by_id(session.scalars(select(Counterparty))),
        invoices=invoices,
        transactions=transactions,
        payroll_runs=payroll,
        obligations=obligations,
        financing_products=_by_id(session.scalars(select(FinancingProduct))),
        flows=flows,
        weeks=project(business.data_as_of, cash, flows, WEEKS),
    )
    session.expunge_all()  # detach: nothing in the snapshot can reach the database again
    return snapshot
