"""Synthetic ledger for "Nordvik Supply Co.", a B2B kitchen-equipment distributor.

Everything here is invented. Amounts are generated as integer cents from a
seeded ``random.Random`` (no floats anywhere), so the same seed always yields
the same rows in the same order.

The planted demo hooks (BUILD_SPEC 5.3) are built explicitly rather than hoped
for, and the operating account's opening balance is calibrated so that the
13-week projection is negative in weeks 6-8 and nowhere else. Every hook is
listed in ``Manifest`` and documented in fixtures/README.md.
"""

from __future__ import annotations

import json
import random
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

from ledgerguard.analytics.cashflow import (
    PaidInvoice,
    ProjectedWeek,
    mean_lateness_days,
    project,
)
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
    CounterpartyType,
    Direction,
    FinancingKind,
    InvoiceStatus,
    InvoiceType,
    ObligationKind,
    PayrollStatus,
    RiskBand,
    TransactionSource,
)
from ledgerguard.money import from_cents, to_cents, to_money

AS_OF = date(2026, 10, 16)  # a Friday; projection week 1 starts Monday 2026-10-19
HISTORY_START = date(2024, 10, 17)  # 24 months of history
HORIZON_END = date(2027, 1, 17)  # end of projection week 13; forward rows stop here
# Confirmed sales and purchase orders reach this far ahead. Beyond it the
# projection uses the SPECULATIVE seasonal fill instead of draft invoices.
ORDER_BOOK_END = date(2026, 11, 20)
VAT_PERCENT = 23
SHORTFALL_WEEKS = (6, 7, 8)

# Monthly seasonality in percent (Jan..Dec): hospitality refits peak in spring and autumn.
SEASON = (70, 85, 115, 125, 120, 100, 90, 75, 110, 115, 100, 95)
BASE_MONTHLY_NET_CENTS = 33_300_000  # about EUR 4.0M a year
COGS_PERCENT = 71
BRENNAN_SHARE_PERCENT = 34

# Calibration targets: the tightest week outside the shortfall keeps at least
# HEADROOM; every shortfall week is at least DEPTH below zero.
CALIBRATION_HEADROOM_CENTS = 1_800_037
CALIBRATION_DEPTH_CENTS = 2_000_000
REFIT_EXPECTED_ON = date(2026, 12, 16)  # Wednesday of projection week 9

RECEIVABLE_INVOICE_ID = "inv_0412"
DUPLICATE_PAYABLE_ID = "inv_0377"


@dataclass(frozen=True)
class CustomerSpec:
    key: str
    name: str
    terms: int
    risk: RiskBand
    weight: int  # share of non-Brennan revenue, percent
    late_mean: int
    late_spread: int


@dataclass(frozen=True)
class SupplierSpec:
    key: str
    name: str
    terms: int
    weight: int  # share of purchases, percent
    invoices_per_month: int
    vat: bool  # False: non-EU supplier, reverse charge, no VAT on the invoice
    blurb: str


BRENNAN = CustomerSpec("brennan", "Brennan Hospitality Group", 30, RiskBand.B, 0, 26, 6)
CUSTOMERS = (
    BRENNAN,
    CustomerSpec("harbourview", "Harbourview Hotels", 30, RiskBand.A, 14, 4, 4),
    CustomerSpec("quayside", "Quayside Bistro Group", 30, RiskBand.B, 10, 9, 5),
    CustomerSpec("larkin", "Larkin & Daughters Catering", 21, RiskBand.B, 8, 6, 4),
    CustomerSpec("cobalt", "Cobalt Street Kitchens", 30, RiskBand.A, 9, 2, 3),
    CustomerSpec("meridian", "Meridian Care Homes", 45, RiskBand.A, 12, 1, 3),
    CustomerSpec("fenwick", "Fenwick University Catering", 60, RiskBand.A, 10, 5, 4),
    CustomerSpec("oakmont", "Oakmont Leisure Centres", 30, RiskBand.A, 7, 3, 3),
    CustomerSpec("tidewater", "Tidewater Restaurants", 30, RiskBand.B, 8, 11, 6),
    CustomerSpec("glenmore", "Glenmore Hotel Collection", 30, RiskBand.A, 11, 3, 3),
    CustomerSpec("ashgrove", "Ashgrove Bakeries", 14, RiskBand.A, 5, 0, 2),
    CustomerSpec("northlight", "Northlight Cafes", 14, RiskBand.B, 6, 4, 3),
)
SUPPLIERS = (
    SupplierSpec(
        "halvorsen",
        "Halvorsen Steelworks AS",
        60,
        38,
        3,
        False,
        "stainless worktops, sinks and fabrication",
    ),
    SupplierSpec(
        "vantor", "Vantor Refrigeration", 45, 22, 2, True, "reach-in fridges, blast chillers"
    ),
    SupplierSpec("kessling", "Kessling Combi Ovens", 45, 20, 2, True, "combi ovens"),
    SupplierSpec("delmar", "Delmar Smallwares", 30, 10, 1, True, "smallwares and GN pans"),
    SupplierSpec("pryce", "Pryce Freight & Logistics", 14, 10, 1, True, "freight and packaging"),
)
OTHER_COUNTERPARTIES = (
    ("payroll", "Staff payroll (net pay, PAYE and PRSI)", CounterpartyType.PAYROLL),
    ("vat", "Tax Authority - VAT", CounterpartyType.TAX),
    ("corptax", "Tax Authority - Corporation Tax", CounterpartyType.TAX),
    ("lessor", "Riverside Industrial Estates Ltd", CounterpartyType.OTHER),
    ("lender", "Fjordline Commercial Lending", CounterpartyType.OTHER),
    ("software", "Stackline Software", CounterpartyType.OTHER),
    ("utilities", "Brightwater Energy", CounterpartyType.SUPPLIER),
    ("fuel", "Fleetline Fuel Card", CounterpartyType.SUPPLIER),
    ("insurer", "Corvane Mutual Insurance", CounterpartyType.OTHER),
    ("bank", "Operating bank - fees and charges", CounterpartyType.OTHER),
    ("shareholders", "Shareholders - dividend account", CounterpartyType.OTHER),
    ("vehicles", "Northway Commercial Vehicles", CounterpartyType.SUPPLIER),
    ("racking", "Stackwell Warehouse Systems", CounterpartyType.SUPPLIER),
)

PETTY_CASH = Decimal("850.00")
LEASE_CENTS = 945_000
LOAN_DRAWDOWN_CENTS = 15_000_000
LOAN_REPAYMENT_CENTS = 469_400
SOFTWARE_CENTS = 124_000
INSURANCE_CENTS = 412_000
CORP_TAX_HISTORY = (
    (date(2025, 6, 23), 1_420_000),
    (date(2025, 12, 8), 1_560_000),
    (date(2026, 6, 23), 1_590_000),
)
CORP_TAX_FORWARD = (date(2026, 12, 8), 1_685_000)
DIVIDENDS = ((date(2025, 4, 30), 5_500_000), (date(2026, 9, 25), 5_500_000))
VAN_PURCHASE = (date(2025, 9, 15), 4_680_000)
RACKING_PURCHASED_ON = AS_OF  # lifts every earlier balance when calibration needs it
RACKING_BASE_CENTS = 12_740_000  # raised by calibration if the history would be overdrawn
HISTORY_LOW_MARGIN_CENTS = 1_500_000
MIN_AS_OF_CASH_CENTS = 2_500_000
MIN_REFIT_NET_CENTS = 6_000_000


def cp_id(key: str) -> str:
    return f"cp_{key}"


# --------------------------------------------------------------------------- dates


def on_or_after_business_day(d: date) -> date:
    while d.weekday() >= 5:
        d += timedelta(days=1)
    return d


def on_or_before_business_day(d: date) -> date:
    while d.weekday() >= 5:
        d -= timedelta(days=1)
    return d


def month_starts(start: date, end: date) -> list[date]:
    out, cur = [], date(start.year, start.month, 1)
    while cur <= end:
        out.append(cur)
        cur = date(cur.year + (cur.month == 12), cur.month % 12 + 1, 1)
    return out


def month_end(first: date) -> date:
    nxt = date(first.year + (first.month == 12), first.month % 12 + 1, 1)
    return nxt - timedelta(days=1)


def clamp_day(year: int, month: int, day: int) -> date:
    return min(date(year, month, 1) + timedelta(days=day - 1), month_end(date(year, month, 1)))


# --------------------------------------------------------------------------- money


def jitter(rng: random.Random, cents: int, percent: int) -> int:
    spread = cents * percent // 100
    return cents + rng.randint(-spread, spread)


def vat_on(net_cents: int) -> int:
    """23% VAT, rounded half up to the cent, in integer arithmetic."""
    return (net_cents * VAT_PERCENT + 50) // 100


def growth_percent(month: date) -> int:
    """About 3% a year of underlying growth, applied monthly."""
    months = (month.year - HISTORY_START.year) * 12 + month.month - HISTORY_START.month
    return 97 + months // 4


# --------------------------------------------------------------------------- builder


@dataclass
class DraftInvoice:
    key: str
    type: InvoiceType
    counterparty: str
    issued_on: date
    due_on: date
    net_cents: int
    tax_cents: int
    status: InvoiceStatus
    paid_on: date | None
    description: str
    id: str = ""

    @property
    def gross_cents(self) -> int:
        return self.net_cents + self.tax_cents


@dataclass
class DraftTxn:
    booked_on: date
    cents: int
    direction: Direction
    counterparty: str
    category: str
    description: str
    source: TransactionSource
    invoice_key: str | None = None
    payroll_id: str | None = None


@dataclass
class Manifest:
    """Ids of the planted demo hooks, for fixtures/README.md and the tests."""

    as_of: date
    shortfall_weeks: tuple[int, ...]
    concentration_customer: str
    overdue_receivables: list[str] = field(default_factory=list)
    disputed_receivable: str = ""
    duplicate_pair: tuple[str, str] = ("", "")  # (legitimate, true duplicate)
    anomalous_transaction: str = ""
    payroll_in_shortfall: str = ""
    brennan_overdue_invoice: str = RECEIVABLE_INVOICE_ID
    big_supplier_payable: str = ""
    brennan_refit_milestone: str = ""
    vat_obligation: str = ""
    opening_balance: str = ""
    as_of_balance: str = ""
    weekly_closing: list[str] = field(default_factory=list)  # all confidence classes
    weekly_closing_with_expected: list[str] = field(default_factory=list)
    weekly_closing_contracted: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class Calibration:
    """The solved-for amounts that place the shortfall (see ``solve_calibration``)."""

    refit_net_cents: int = 0
    deposit_cents: int = 0
    racking_cents: int = RACKING_BASE_CENTS


class NordvikBuilder:
    def __init__(self, seed: int, cal: Calibration) -> None:
        self.rng = random.Random(seed)
        self.cal = cal
        self.invoices: list[DraftInvoice] = []
        self.txns: list[DraftTxn] = []
        self.payroll: list[PayrollRun] = []
        self.obligations: list[Obligation] = []
        self._key_counter = 0

    # ---- helpers

    def _key(self) -> str:
        self._key_counter += 1
        return f"k{self._key_counter:05d}"

    def _business_day_in(self, first: date, last: date) -> date:
        d = first + timedelta(days=self.rng.randint(0, (last - first).days))
        d = on_or_after_business_day(d)
        return d if d <= last else on_or_before_business_day(last)

    # ---- receivables

    def _receivable(self, spec: CustomerSpec, issued: date, net_cents: int, what: str) -> None:
        due = issued + timedelta(days=spec.terms)
        late = spec.late_mean + self.rng.randint(-spec.late_spread, spec.late_spread)
        paid = on_or_after_business_day(max(due + timedelta(days=late), issued + timedelta(1)))
        if paid > AS_OF and due <= AS_OF:
            # Only the planted invoices are past due at the as-of date (5.3), so a late
            # payment that would cross the as-of date lands somewhere before it instead.
            paid = self._business_day_in(max(due, issued + timedelta(days=1)), AS_OF)
        if issued > AS_OF:
            status = InvoiceStatus.DRAFT  # confirmed order, scheduled for billing
        elif paid <= AS_OF:
            status = InvoiceStatus.PAID
        else:
            status = InvoiceStatus.SENT
        self.invoices.append(
            DraftInvoice(
                key=self._key(),
                type=InvoiceType.RECEIVABLE,
                counterparty=cp_id(spec.key),
                issued_on=issued,
                due_on=due,
                net_cents=net_cents,
                tax_cents=vat_on(net_cents),
                status=status,
                paid_on=paid if status is InvoiceStatus.PAID else None,
                description=f"{spec.name} - {what}",
            )
        )

    def generate_sales(self) -> None:
        for first in month_starts(HISTORY_START, HORIZON_END):
            last = month_end(first)
            total = (
                BASE_MONTHLY_NET_CENTS * SEASON[first.month - 1] * growth_percent(first) // 10_000
            )
            others = total * (100 - BRENNAN_SHARE_PERCENT) // 100
            brennan = total - others
            for i in range(3):
                issued = self._business_day_in(first, last)
                if HISTORY_START <= issued <= ORDER_BOOK_END:
                    self._receivable(
                        BRENNAN,
                        issued,
                        jitter(self.rng, brennan // 3, 10),
                        f"site fit-out order {first:%b %Y} #{i + 1}",
                    )
            for spec in CUSTOMERS[1:]:
                count = 2 if spec.weight >= 10 else 1
                for i in range(count):
                    issued = self._business_day_in(first, last)
                    net = jitter(self.rng, others * spec.weight // 100 // count, 15)
                    if HISTORY_START <= issued <= ORDER_BOOK_END:
                        self._receivable(
                            spec, issued, net, f"equipment order {first:%b %Y} #{i + 1}"
                        )

    # ---- payables

    def _payable(
        self,
        spec: SupplierSpec,
        issued: date,
        net_cents: int,
        description: str,
        status: InvoiceStatus | None = None,
        paid_on: date | None = None,
    ) -> DraftInvoice:
        due = issued + timedelta(days=spec.terms)
        if status is None:
            pay = on_or_before_business_day(due)
            if issued > AS_OF:
                status = InvoiceStatus.DRAFT  # confirmed purchase order, not yet invoiced
            elif pay <= AS_OF:
                status = InvoiceStatus.PAID
            else:
                status = InvoiceStatus.SENT
            paid_on = pay if status is InvoiceStatus.PAID else None
        inv = DraftInvoice(
            key=self._key(),
            type=InvoiceType.PAYABLE,
            counterparty=cp_id(spec.key),
            issued_on=issued,
            due_on=due,
            net_cents=net_cents,
            tax_cents=vat_on(net_cents) if spec.vat else 0,
            status=status,
            paid_on=paid_on,
            description=description,
        )
        self.invoices.append(inv)
        return inv

    def generate_purchases(self) -> None:
        for first in month_starts(HISTORY_START, HORIZON_END):
            last = month_end(first)
            # Stock is bought a month ahead of the sales it serves.
            season = SEASON[first.month % 12]
            total = (
                BASE_MONTHLY_NET_CENTS
                * season
                * growth_percent(first)
                // 10_000
                * COGS_PERCENT
                // 100
            )
            for spec in SUPPLIERS:
                per_invoice = total * spec.weight // 100 // spec.invoices_per_month
                for _ in range(spec.invoices_per_month):
                    issued = self._business_day_in(first, last)
                    net = jitter(self.rng, per_invoice, 12)
                    if HISTORY_START <= issued <= ORDER_BOOK_END:
                        self._payable(spec, issued, net, f"{spec.name} - {spec.blurb}")

    # ---- recurring cash movements without invoices

    def _txn(
        self,
        booked: date,
        cents: int,
        direction: Direction,
        cp: str,
        category: str,
        description: str,
        source: TransactionSource = TransactionSource.BANK_FEED,
        **links: Any,
    ) -> None:
        if HISTORY_START <= booked <= AS_OF:
            self.txns.append(
                DraftTxn(
                    booked, cents, direction, cp_id(cp), category, description, source, **links
                )
            )

    def generate_recurring(self) -> None:
        for first in month_starts(HISTORY_START, AS_OF):
            y, m = first.year, first.month
            self._txn(
                on_or_after_business_day(first),
                LEASE_CENTS,
                Direction.OUT,
                "lessor",
                "lease",
                "Unit 4 warehouse lease - monthly rent",
            )
            if first > date(2024, 11, 1):
                self._txn(
                    on_or_after_business_day(first),
                    LOAN_REPAYMENT_CENTS,
                    Direction.OUT,
                    "lender",
                    "loan_repayment",
                    "Term loan repayment",
                )
            self._txn(
                on_or_after_business_day(clamp_day(y, m, 5)),
                SOFTWARE_CENTS,
                Direction.OUT,
                "software",
                "software",
                "ERP and accounting subscription",
            )
            self._txn(
                on_or_after_business_day(clamp_day(y, m, 12)),
                jitter(self.rng, 184_000, 12),
                Direction.OUT,
                "utilities",
                "utilities",
                "Electricity and gas - warehouse",
            )
            self._txn(
                on_or_before_business_day(month_end(first)),
                jitter(self.rng, 6_200, 15),
                Direction.OUT,
                "bank",
                "bank_fees",
                "Account fees and transfer charges",
            )
            if m in (1, 4, 7, 10):
                self._txn(
                    on_or_after_business_day(clamp_day(y, m, 15)),
                    INSURANCE_CENTS,
                    Direction.OUT,
                    "insurer",
                    "insurance",
                    "Commercial combined policy",
                )
        monday = HISTORY_START + timedelta(days=(7 - HISTORY_START.weekday()) % 7)
        while monday <= AS_OF:
            self._txn(
                monday,
                jitter(self.rng, 64_000, 12),
                Direction.OUT,
                "fuel",
                "fuel",
                "Fleetline Fuel Card - weekly settlement",
            )
            monday += timedelta(days=7)
        self._txn(
            date(2024, 11, 4),
            LOAN_DRAWDOWN_CENTS,
            Direction.IN,
            "lender",
            "loan_drawdown",
            "Term loan drawdown - 36 month working-capital loan",
        )
        for paid, cents in DIVIDENDS:
            self._txn(
                paid,
                cents,
                Direction.OUT,
                "shareholders",
                "dividend",
                f"Final dividend {paid.year - 1}",
            )
        self._txn(
            VAN_PURCHASE[0],
            VAN_PURCHASE[1],
            Direction.OUT,
            "vehicles",
            "capex",
            "Delivery van - outright purchase",
        )
        self._txn(
            RACKING_PURCHASED_ON,
            self.cal.racking_cents,
            Direction.OUT,
            "racking",
            "capex",
            "Warehouse racking and electric forklifts - paid in full from operating cash",
        )
        for paid, cents in CORP_TAX_HISTORY:
            self._txn(
                on_or_before_business_day(paid),
                cents,
                Direction.OUT,
                "corptax",
                "corporation_tax",
                "Corporation tax - preliminary instalment",
            )

    def generate_payroll(self) -> None:
        gross_by_year = {2024: 6_185_000, 2025: 6_401_500, 2026: 6_625_500, 2027: 6_857_400}
        runs: list[tuple[date, date, date, int]] = []
        for first in month_starts(HISTORY_START, HORIZON_END):
            pay = on_or_before_business_day(clamp_day(first.year, first.month, 28))
            if pay < HISTORY_START or pay > HORIZON_END:
                continue
            runs.append((first, month_end(first), pay, gross_by_year[first.year]))
        for n, (start, end, pay, gross) in enumerate(runs, start=1):
            employer = (gross * 11105 + 5000) // 10000  # employer PRSI 11.05%
            status = PayrollStatus.PAID if pay <= AS_OF else PayrollStatus.SCHEDULED
            run = PayrollRun(
                id=f"pay_{n:04d}",
                period_start=start,
                period_end=end,
                pay_date=pay,
                gross_total=from_cents(gross),
                employer_cost_total=from_cents(employer),
                status=status,
            )
            self.payroll.append(run)
            if status is PayrollStatus.PAID:
                self._txn(
                    pay,
                    employer,
                    Direction.OUT,
                    "payroll",
                    "payroll",
                    f"Payroll {start:%B %Y}",
                    payroll_id=run.id,
                )

    # ---- VAT: bi-monthly periods, due on the 23rd of the month after the period

    def vat_due_cents(self, period_start: date, period_end: date) -> int:
        output = sum(
            i.tax_cents
            for i in self.invoices
            if i.type is InvoiceType.RECEIVABLE and period_start <= i.issued_on <= period_end
        )
        inputs = sum(
            i.tax_cents
            for i in self.invoices
            if i.type is InvoiceType.PAYABLE and period_start <= i.issued_on <= period_end
        )
        return output - inputs

    def vat_periods(self) -> list[tuple[date, date, date]]:
        periods = []
        for year in (2024, 2025, 2026):
            for start_month in (1, 3, 5, 7, 9, 11):
                start = date(year, start_month, 1)
                end = month_end(date(year, start_month + 1, 1))
                due_month = start_month + 2
                due = date(year + 1, 1, 23) if due_month > 12 else date(year, due_month, 23)
                periods.append((start, end, due))
        return periods

    def generate_vat_history(self) -> None:
        periods = self.vat_periods()
        for n, (start, end, due) in enumerate(periods):
            pay = on_or_before_business_day(due)
            if HISTORY_START <= pay <= AS_OF:
                # A period that began before the ledger history is estimated from the
                # next full period, since its early sales are not in the ledger.
                s, e, _ = periods[n + 1] if start < HISTORY_START else (start, end, due)
                cents = self.vat_due_cents(s, e)
                if cents <= 0:
                    raise AssertionError(f"VAT period {start}..{end} produced a refund")
                self._txn(
                    pay, cents, Direction.OUT, "vat", "vat", f"VAT return {start:%b}-{end:%b %Y}"
                )

    # ---- planted demo hooks (BUILD_SPEC 5.3)

    def _planted(
        self,
        key: str,
        type_: InvoiceType,
        cp: str,
        issued: date,
        due: date,
        net_cents: int,
        vat: bool,
        status: InvoiceStatus,
        description: str,
        paid_on: date | None = None,
    ) -> DraftInvoice:
        inv = DraftInvoice(
            key=key,
            type=type_,
            counterparty=cp_id(cp),
            issued_on=issued,
            due_on=due,
            net_cents=net_cents,
            tax_cents=vat_on(net_cents) if vat else 0,
            status=status,
            paid_on=paid_on,
            description=description,
        )
        self.invoices.append(inv)
        return inv

    def plant_invoices(self) -> None:
        overdue = InvoiceStatus.OVERDUE
        recv, pay = InvoiceType.RECEIVABLE, InvoiceType.PAYABLE
        self._planted(
            "brennan_overdue",
            recv,
            "brennan",
            date(2026, 9, 2),
            date(2026, 10, 2),
            3_809_268,
            True,
            overdue,
            "Brennan Hospitality Group - combi ovens and blast chillers, Harbour Street site",
        )
        self._planted(
            "quayside_overdue",
            recv,
            "quayside",
            date(2026, 8, 26),
            date(2026, 9, 25),
            726_840,
            True,
            overdue,
            "Quayside Bistro Group - induction ranges and extraction hoods",
        )
        self._planted(
            "larkin_overdue",
            recv,
            "larkin",
            date(2026, 9, 18),
            date(2026, 10, 9),
            424_211,
            True,
            overdue,
            "Larkin & Daughters Catering - mobile hot cupboards",
        )
        self._planted(
            "harbourview_disputed",
            recv,
            "harbourview",
            date(2026, 8, 19),
            date(2026, 9, 18),
            1_008_130,
            True,
            InvoiceStatus.DISPUTED,
            "Harbourview Hotels - six-tray combi oven. Customer disputes delivery "
            "condition (damaged door seal).",
        )
        vendor_ref = "Delmar Smallwares invoice DSW-26-0918 - chafing dishes and GN pans"
        self._planted(
            "delmar_legit",
            pay,
            "delmar",
            date(2026, 9, 8),
            date(2026, 10, 8),
            350_610,
            True,
            InvoiceStatus.PAID,
            vendor_ref,
            paid_on=date(2026, 10, 8),
        )
        self._planted(
            "delmar_duplicate",
            pay,
            "delmar",
            date(2026, 9, 11),
            date(2026, 10, 11),
            350_610,
            True,
            overdue,
            vendor_ref,
        )
        self._planted(
            "halvorsen_stock_build",
            pay,
            "halvorsen",
            date(2026, 10, 2),
            date(2026, 12, 1),
            14_250_000,
            False,
            InvoiceStatus.SENT,
            "Halvorsen Steelworks AS - Q1 stainless fabrication stock build "
            "(bulk order, reverse-charge VAT)",
        )

    def plant_stock_deposit(self) -> None:
        """First instalment on the refit stock, due in week 6, when calibration needs it."""
        if self.cal.deposit_cents > 0:
            self._planted(
                "halvorsen_deposit",
                InvoiceType.PAYABLE,
                "halvorsen",
                date(2026, 10, 2),
                date(2026, 11, 25),
                self.cal.deposit_cents,
                False,
                InvoiceStatus.SENT,
                "Halvorsen Steelworks AS - Q1 stainless stock build, first instalment "
                "(reverse-charge VAT)",
            )

    def plant_refit_milestone(self, brennan_lateness: int) -> None:
        """Brennan's refit-programme milestone, invoiced on the as-of date.

        Contract terms are set so that, at Brennan's usual lateness, the money
        arrives in week 9, after the stock bought for the refit has been paid
        for in week 7. That timing gap is the shortfall. The amount is solved
        for by ``solve_calibration``.
        """
        net_cents = self.cal.refit_net_cents
        if net_cents <= 0:
            return
        due = REFIT_EXPECTED_ON - timedelta(days=brennan_lateness)
        if (due - AS_OF).days < BRENNAN.terms:
            raise AssertionError("refit milestone terms would be shorter than standard terms")
        self._planted(
            "brennan_refit",
            InvoiceType.RECEIVABLE,
            "brennan",
            AS_OF,
            due,
            net_cents,
            True,
            InvoiceStatus.SENT,
            "Brennan Hospitality Group - 12-kitchen refit programme, milestone 3 "
            f"(contract terms {(due - AS_OF).days} days)",
        )

    def plant_anomaly(self) -> DraftTxn:
        """One fuel-card charge about 8 robust standard deviations above the norm."""
        fuel = sorted(t.cents for t in self.txns if t.category == "fuel")
        median = _median(fuel)
        mad = _median(sorted(abs(Decimal(c) - median) for c in fuel))
        cents = int((median + Decimal(8) * Decimal("1.4826") * mad).to_integral_value())
        txn = DraftTxn(
            date(2026, 9, 22),
            cents,
            Direction.OUT,
            cp_id("fuel"),
            "fuel",
            "Fleetline Fuel Card - fleet charge",
            TransactionSource.BANK_FEED,
        )
        self.txns.append(txn)
        return txn

    # ---- forward obligations

    def generate_obligations(self) -> None:
        rows: list[tuple[date, ObligationKind, int, str | None, str, str]] = []
        for first in month_starts(AS_OF + timedelta(days=1), HORIZON_END):
            y, m = first.year, first.month
            rows.append(
                (
                    first,
                    ObligationKind.LEASE,
                    LEASE_CENTS,
                    "FREQ=MONTHLY;BYMONTHDAY=1",
                    "lessor",
                    "Unit 4 warehouse lease - monthly rent",
                )
            )
            rows.append(
                (
                    first,
                    ObligationKind.LOAN_REPAYMENT,
                    LOAN_REPAYMENT_CENTS,
                    "FREQ=MONTHLY;BYMONTHDAY=1",
                    "lender",
                    "Term loan repayment",
                )
            )
            rows.append(
                (
                    clamp_day(y, m, 5),
                    ObligationKind.SUBSCRIPTION,
                    SOFTWARE_CENTS,
                    "FREQ=MONTHLY;BYMONTHDAY=5",
                    "software",
                    "ERP and accounting subscription",
                )
            )
        for start, end, due in self.vat_periods():
            if AS_OF < due <= HORIZON_END:
                rows.append(
                    (
                        due,
                        ObligationKind.TAX,
                        self.vat_due_cents(start, end),
                        "FREQ=MONTHLY;INTERVAL=2;BYMONTHDAY=23",
                        "vat",
                        f"VAT return {start:%b}-{end:%b %Y} (from invoices issued in the period)",
                    )
                )
        due, cents = CORP_TAX_FORWARD
        rows.append(
            (
                due,
                ObligationKind.TAX,
                cents,
                None,
                "corptax",
                "Corporation tax - second preliminary instalment 2026",
            )
        )
        rows = [r for r in rows if AS_OF < r[0] <= HORIZON_END]
        rows.sort(key=lambda r: (r[0], r[1].value))
        for n, (due_on, kind, cents, rrule, cp, text) in enumerate(rows, start=1):
            self.obligations.append(
                Obligation(
                    id=f"obl_{n:04d}",
                    kind=kind,
                    due_on=due_on,
                    amount=from_cents(cents),
                    recurrence_rule=rrule,
                    counterparty_id=cp_id(cp),
                    description=text,
                )
            )

    # ---- settlements and ids

    def settle_invoices(self) -> None:
        for inv in self.invoices:
            if inv.paid_on is None:
                continue
            receivable = inv.type is InvoiceType.RECEIVABLE
            self.txns.append(
                DraftTxn(
                    inv.paid_on,
                    inv.gross_cents,
                    Direction.IN if receivable else Direction.OUT,
                    inv.counterparty,
                    "customer_receipt" if receivable else "supplier_payment",
                    f"{'Receipt' if receivable else 'Payment'} - {inv.description}",
                    TransactionSource.INVOICE_SETTLEMENT,
                    invoice_key=inv.key,
                )
            )

    def assign_invoice_ids(self) -> None:
        self.invoices.sort(
            key=lambda i: (i.issued_on, i.type.value, i.counterparty, i.net_cents, i.key)
        )
        for n, inv in enumerate(self.invoices, start=1):
            inv.id = f"inv_{n:04d}"
        # The demo script refers to "invoice 0412" and "supplier invoice 0377", so those
        # two numbers are swapped onto the planted rows. Invoice numbers are therefore
        # not strictly chronological; see fixtures/README.md.
        for key, wanted in (
            ("brennan_overdue", RECEIVABLE_INVOICE_ID),
            ("delmar_duplicate", DUPLICATE_PAYABLE_ID),
        ):
            planted = self.by_key(key)
            holder = next(i for i in self.invoices if i.id == wanted)
            planted.id, holder.id = holder.id, planted.id
        self.invoices.sort(key=lambda i: i.id)

    def by_key(self, key: str) -> DraftInvoice:
        return next(i for i in self.invoices if i.key == key)


def _median(sorted_values: list[Any]) -> Decimal:
    n = len(sorted_values)
    mid = n // 2
    if n % 2:
        return Decimal(sorted_values[mid])
    return (Decimal(sorted_values[mid - 1]) + Decimal(sorted_values[mid])) / 2


def _txn_sort_key(t: DraftTxn) -> tuple[Any, ...]:
    return (
        t.booked_on,
        t.direction.value,
        t.category,
        t.counterparty,
        t.cents,
        t.invoice_key or "",
        t.payroll_id or "",
    )


# --------------------------------------------------------------------------- assembly


def _load_financing_products(path: Path) -> list[FinancingProduct]:
    def no_floats(text: str) -> Decimal:
        raise ValueError(f"financing catalogue must quote every number with a fraction: {text}")

    raw = json.loads(path.read_text(encoding="utf-8"), parse_float=no_floats)
    if not raw.get("synthetic"):
        raise ValueError("financing catalogue must be marked synthetic")
    products = []
    for p in raw["products"]:
        products.append(
            FinancingProduct(
                id=p["id"],
                provider=p["provider"],
                name=p["name"],
                kind=FinancingKind(p["kind"]),
                min_amount=to_money(p["min_amount"]),
                max_amount=to_money(p["max_amount"]),
                nominal_rate=Decimal(p["nominal_rate"]),
                fee_structure=p["fee_structure"],
                term_months=p["term_months"],
                time_to_funding_days=p["time_to_funding_days"],
                eligibility_rules=p["eligibility_rules"],
                requires_collateral=p["requires_collateral"],
            )
        )
    return products


@dataclass
class SeedResult:
    rows: list[Base]
    manifest: Manifest


@dataclass
class _Assembly:
    builder: NordvikBuilder
    invoices: list[Invoice]
    transactions: list[Transaction]
    key_to_id: dict[str, str]
    anomaly_id: str
    weeks: list[ProjectedWeek]  # projection from a zero opening balance


def _assemble(seed: int, cal: Calibration) -> _Assembly:
    b = NordvikBuilder(seed, cal)
    b.generate_sales()
    b.generate_purchases()
    b.generate_recurring()
    b.generate_payroll()
    b.plant_invoices()
    lateness = mean_lateness_days(
        PaidInvoice(i.counterparty, i.due_on, i.paid_on)
        for i in b.invoices
        if i.type is InvoiceType.RECEIVABLE and i.paid_on is not None
    )
    b.plant_refit_milestone(lateness[cp_id("brennan")])
    b.plant_stock_deposit()
    b.generate_vat_history()
    anomaly = b.plant_anomaly()
    b.generate_obligations()
    b.settle_invoices()
    b.assign_invoice_ids()

    key_to_id = {i.key: i.id for i in b.invoices}
    b.txns.sort(key=_txn_sort_key)
    txn_ids = {id(t): f"txn_{n:04d}" for n, t in enumerate(b.txns, start=1)}
    invoices = [
        Invoice(
            id=i.id,
            type=i.type,
            counterparty_id=i.counterparty,
            issued_on=i.issued_on,
            due_on=i.due_on,
            gross_amount=from_cents(i.gross_cents),
            tax_amount=from_cents(i.tax_cents),
            net_amount=from_cents(i.net_cents),
            status=i.status,
            paid_amount=from_cents(i.gross_cents if i.status is InvoiceStatus.PAID else 0),
            description=i.description,
        )
        for i in b.invoices
    ]
    transactions = [
        Transaction(
            id=txn_ids[id(t)],
            account_id="acc_operating",
            booked_on=t.booked_on,
            value_date=t.booked_on,
            amount=from_cents(t.cents),
            direction=t.direction,
            counterparty_id=t.counterparty,
            category=t.category,
            description=t.description,
            source=t.source,
            reconciled=t.booked_on <= AS_OF - timedelta(days=7),
            invoice_id=key_to_id[t.invoice_key] if t.invoice_key else None,
            payroll_run_id=t.payroll_id,
        )
        for t in b.txns
    ]
    flows = projection_flows(AS_OF, 13, invoices, transactions, b.payroll, b.obligations)
    return _Assembly(
        b,
        invoices,
        transactions,
        key_to_id,
        txn_ids[id(anomaly)],
        project(AS_OF, Decimal("0.00"), flows),
    )


def _ceil_to(cents: int, step: int) -> int:
    return -(-cents // step) * step


def solve_calibration(weeks: list[ProjectedWeek], seed: int) -> tuple[int, Calibration]:
    """Solve for as-of cash ``A``, refit milestone net ``N`` and stock deposit ``Q`` (cents).

    ``weeks`` is the projection from a zero opening with no milestone and no
    deposit; ``C_k`` is its week-k closing. The milestone's gross (1.23N) is
    received in week 9 and its VAT (0.23N) is paid with the week-6 return; the
    deposit is paid in week 6. The constraints are:

    * weeks 1-5:  A + C_k                >= headroom
    * weeks 6-8:  A + C_k - 0.23N - Q    <= -depth
    * weeks 9-13: A + C_k + N - Q        >= headroom

    The first choice is no deposit and as much cash as the shortfall allows,
    which keeps the milestone small. If weeks 1-5 need more cash than that,
    cash is set to their minimum and the deposit deepens week 6 instead.
    """
    c = {w.index: to_cents(w.closing) for w in weeks}
    head, depth = CALIBRATION_HEADROOM_CENTS, CALIBRATION_DEPTH_CENTS
    k = head - min(c[i] for i in range(9, 14))
    m = max(c[i] for i in SHORTFALL_WEEKS)
    floor_cash = max(head - min(c[i] for i in range(1, 6)), MIN_AS_OF_CASH_CENTS)
    # With Q = 0 and N = k - A:  A <= (100 (-depth - m) + 23 k) / 123
    cash = (100 * (-depth - m) + VAT_PERCENT * k) // (100 + VAT_PERCENT)
    deposit = 0
    if cash < floor_cash:
        cash = floor_cash
        # 1.23 Q >= A + depth + m - 0.23 (k - A)
        need = 100 * (cash + depth + m) - VAT_PERCENT * (k - cash)
        deposit = _ceil_to(-(-need // (100 + VAT_PERCENT)), 10_000)
    refit = max(_ceil_to(k - cash + deposit, 100_000), MIN_REFIT_NET_CENTS)
    return cash, Calibration(refit_net_cents=refit, deposit_cents=deposit)


def _history_low(opening: Decimal, transactions: Iterable[Transaction]) -> Decimal:
    balance = low = opening
    for t in sorted(transactions, key=lambda t: (t.booked_on, t.direction.value != "in")):
        balance += t.amount if t.direction is Direction.IN else -t.amount
        low = min(low, balance)
    return low


def _net(transactions: Iterable[Transaction]) -> Decimal:
    return sum(
        (t.amount if t.direction is Direction.IN else -t.amount for t in transactions),
        Decimal("0.00"),
    )


def build(seed: int, financing_catalogue: Path) -> SeedResult:
    probe = _assemble(seed, Calibration())
    cash_cents, cal = solve_calibration(probe.weeks, seed)
    a = _assemble(seed, cal)
    as_of_balance = from_cents(cash_cents)
    closings = [as_of_balance + w.closing for w in a.weeks]
    for w, closing in zip(a.weeks, closings, strict=True):
        short = w.index in SHORTFALL_WEEKS
        if short != (closing < 0):
            raise AssertionError(f"seed {seed}: week {w.index} closes at {closing}")

    # The projection counts every bank and cash account; petty cash has no movements.
    opening = as_of_balance - PETTY_CASH - _net(a.transactions)
    shortfall = from_cents(HISTORY_LOW_MARGIN_CENTS) - _history_low(opening, a.transactions)
    if shortfall > 0:
        # A larger October equipment purchase raises every earlier balance by the
        # same amount without touching the as-of cash or the projection.
        cal = Calibration(
            cal.refit_net_cents,
            cal.deposit_cents,
            RACKING_BASE_CENTS + _ceil_to(to_cents(shortfall), 100_000),
        )
        a = _assemble(seed, cal)
        opening = as_of_balance - PETTY_CASH - _net(a.transactions)
    _check_history_never_overdrawn(opening, a.transactions)

    b, key_to_id = a.builder, a.key_to_id
    manifest = Manifest(
        as_of=AS_OF,
        shortfall_weeks=SHORTFALL_WEEKS,
        concentration_customer=cp_id("brennan"),
        overdue_receivables=sorted(
            key_to_id[k] for k in ("brennan_overdue", "quayside_overdue", "larkin_overdue")
        ),
        disputed_receivable=key_to_id["harbourview_disputed"],
        duplicate_pair=(key_to_id["delmar_legit"], key_to_id["delmar_duplicate"]),
        anomalous_transaction=a.anomaly_id,
        payroll_in_shortfall=next(
            r.id
            for r in b.payroll
            if any(
                w.starts_on <= r.pay_date <= w.ends_on
                for w in a.weeks
                if w.index in SHORTFALL_WEEKS
            )
        ),
        big_supplier_payable=key_to_id["halvorsen_stock_build"],
        brennan_refit_milestone=key_to_id["brennan_refit"],
        vat_obligation=next(o.id for o in b.obligations if o.counterparty_id == cp_id("vat")),
        opening_balance=str(opening),
        as_of_balance=str(as_of_balance),
        weekly_closing=[str(c) for c in closings],
        weekly_closing_with_expected=[
            str(as_of_balance + w.closing_with_expected) for w in a.weeks
        ],
        weekly_closing_contracted=[str(as_of_balance + w.closing_contracted) for w in a.weeks],
    )

    rows: list[Base] = [
        Business(
            id="biz_nordvik",
            legal_name="Nordvik Supply Co. Ltd",
            country="IE",
            currency="EUR",
            industry_code="NACE 46.69",
            founded_on=date(2012, 3, 14),
            employee_count=18,
            fiscal_year_start="01-01",
            data_as_of=AS_OF,
        ),
        Account(
            id="acc_operating",
            name="Operating account",
            type=AccountType.BANK,
            currency="EUR",
            opening_balance=opening,
            as_of=HISTORY_START,
            credit_limit=None,
        ),
        Account(
            id="acc_credit_line",
            name="Revolving credit facility",
            type=AccountType.CREDIT_LINE,
            currency="EUR",
            opening_balance=Decimal("0.00"),
            as_of=HISTORY_START,
            credit_limit=Decimal("100000.00"),
        ),
        Account(
            id="acc_petty_cash",
            name="Petty cash",
            type=AccountType.CASH,
            currency="EUR",
            opening_balance=PETTY_CASH,
            as_of=HISTORY_START,
            credit_limit=None,
        ),
    ]
    rows += _counterparties()
    rows += _load_financing_products(financing_catalogue)
    rows += b.payroll
    rows += a.invoices
    rows += a.transactions
    rows += b.obligations
    return SeedResult(rows=rows, manifest=manifest)


def _counterparties() -> list[Counterparty]:
    rows = [
        Counterparty(
            id=cp_id(c.key),
            name=c.name,
            type=CounterpartyType.CUSTOMER,
            payment_terms_days=c.terms,
            risk_band=c.risk,
            is_whitelisted=False,
        )
        for c in CUSTOMERS
    ]
    rows += [
        Counterparty(
            id=cp_id(s.key),
            name=s.name,
            type=CounterpartyType.SUPPLIER,
            payment_terms_days=s.terms,
            risk_band=RiskBand.A,
            is_whitelisted=True,
        )
        for s in SUPPLIERS
    ]
    rows += [
        Counterparty(
            id=cp_id(key),
            name=name,
            type=type_,
            payment_terms_days=0,
            risk_band=RiskBand.A,
            is_whitelisted=True,
        )
        for key, name, type_ in OTHER_COUNTERPARTIES
    ]
    return sorted(rows, key=lambda c: c.id)


def _check_history_never_overdrawn(opening: Decimal, transactions: Iterable[Transaction]) -> None:
    low = _history_low(opening, transactions)
    if opening < Decimal("20000.00") or low < from_cents(HISTORY_LOW_MARGIN_CENTS):
        raise AssertionError(f"implausible history: opening {opening}, lowest balance {low}")
