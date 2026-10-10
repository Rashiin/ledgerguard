"""Turn ledger rows into projection inputs.

These functions accept any objects with the model's attributes, whether ORM
rows or unsaved instances. The seeder and the API therefore share one
definition of an expected flow and of its confidence class:

* CONTRACTED: invoices that are sent, part paid or overdue, scheduled payroll,
  and tax, loan, lease and subscription obligations;
* EXPECTED: draft invoices, which are the confirmed order book;
* SPECULATIVE: the seasonal fill after the order book, from ``seasonal_fill``.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from datetime import date, timedelta
from decimal import Decimal
from typing import Protocol

from ledgerguard.analytics.cashflow import (
    ExpectedFlow,
    PaidInvoice,
    expected_receipt_date,
    horizon_end,
    mean_lateness_days,
)
from ledgerguard.domain.enums import (
    Confidence,
    Direction,
    InvoiceStatus,
    InvoiceType,
    PayrollStatus,
    TransactionSource,
)

ISSUED_OPEN = {InvoiceStatus.SENT, InvoiceStatus.PART_PAID, InvoiceStatus.OVERDUE}
# 52 weeks, so a fill line falls on the same weekday as the invoice it repeats.
SAME_WEEK_LAST_YEAR = timedelta(days=364)


class InvoiceLike(Protocol):
    id: str
    type: InvoiceType
    counterparty_id: str
    issued_on: date
    due_on: date
    gross_amount: Decimal
    paid_amount: Decimal
    status: InvoiceStatus


class TransactionLike(Protocol):
    invoice_id: str | None
    booked_on: date
    source: TransactionSource


class PayrollLike(Protocol):
    id: str
    pay_date: date
    employer_cost_total: Decimal
    status: PayrollStatus


class ObligationLike(Protocol):
    id: str
    due_on: date
    amount: Decimal
    description: str


def paid_receivable_history(
    invoices: Iterable[InvoiceLike], transactions: Iterable[TransactionLike]
) -> list[PaidInvoice]:
    """Fully paid receivables with the date of their last settlement."""
    last_paid: dict[str, date] = {}
    for txn in transactions:
        if txn.source is TransactionSource.INVOICE_SETTLEMENT and txn.invoice_id:
            prev = last_paid.get(txn.invoice_id)
            if prev is None or txn.booked_on > prev:
                last_paid[txn.invoice_id] = txn.booked_on
    return [
        PaidInvoice(inv.counterparty_id, inv.due_on, last_paid[inv.id])
        for inv in invoices
        if inv.type is InvoiceType.RECEIVABLE
        and inv.status is InvoiceStatus.PAID
        and inv.id in last_paid
    ]


def _cash_date(inv: InvoiceLike, due_on: date, lateness: Mapping[str, int]) -> date:
    if inv.type is InvoiceType.RECEIVABLE:
        return expected_receipt_date(due_on, lateness.get(inv.counterparty_id, 0))
    return due_on


def _invoice_label(inv: InvoiceLike, lateness: Mapping[str, int]) -> str:
    if inv.type is InvoiceType.RECEIVABLE:
        late = lateness.get(inv.counterparty_id, 0)
        kind = "Order book receivable" if inv.status is InvoiceStatus.DRAFT else "Receivable"
        return f"{kind} {inv.id}, due {inv.due_on}, usually paid {late} days late"
    kind = "Order book payable" if inv.status is InvoiceStatus.DRAFT else "Payable"
    return f"{kind} {inv.id}, due {inv.due_on}"


def expected_flows(
    invoices: Iterable[InvoiceLike],
    payroll_runs: Iterable[PayrollLike],
    obligations: Iterable[ObligationLike],
    lateness: Mapping[str, int],
) -> list[ExpectedFlow]:
    """CONTRACTED and EXPECTED lines from ledger rows. Disputed invoices are excluded."""
    flows: list[ExpectedFlow] = []
    for inv in invoices:
        outstanding = inv.gross_amount - inv.paid_amount
        if outstanding <= 0:
            continue
        if inv.status in ISSUED_OPEN:
            confidence = Confidence.CONTRACTED
        elif inv.status is InvoiceStatus.DRAFT:
            confidence = Confidence.EXPECTED
        else:
            continue
        flows.append(
            ExpectedFlow(
                on=_cash_date(inv, inv.due_on, lateness),
                amount=outstanding,
                direction=Direction.IN if inv.type is InvoiceType.RECEIVABLE else Direction.OUT,
                confidence=confidence,
                source_table="invoice",
                source_id=inv.id,
                label=_invoice_label(inv, lateness),
            )
        )
    for run in payroll_runs:
        if run.status is PayrollStatus.SCHEDULED:
            flows.append(
                ExpectedFlow(
                    on=run.pay_date,
                    amount=run.employer_cost_total,
                    direction=Direction.OUT,
                    confidence=Confidence.CONTRACTED,
                    source_table="payroll_run",
                    source_id=run.id,
                    label=f"Payroll {run.id}",
                )
            )
    for ob in obligations:
        flows.append(
            ExpectedFlow(
                on=ob.due_on,
                amount=ob.amount,
                direction=Direction.OUT,
                confidence=Confidence.CONTRACTED,
                source_table="obligation",
                source_id=ob.id,
                label=ob.description,
            )
        )
    return flows


def seasonal_fill(
    as_of: date,
    until: date,
    invoices: Iterable[InvoiceLike],
    lateness: Mapping[str, int],
) -> list[ExpectedFlow]:
    """SPECULATIVE lines for issue dates beyond the order book.

    The rule is one an owner can check by hand. After the last draft invoice
    on each side, assume every counterparty repeats what it invoiced
    in the same week last year, on the same terms, paid with its usual
    lateness. Each line cites the historical invoice it repeats. No growth
    or trend adjustment is applied.
    """
    rows = list(invoices)
    flows: list[ExpectedFlow] = []
    for side in (InvoiceType.RECEIVABLE, InvoiceType.PAYABLE):
        drafts = [i.issued_on for i in rows if i.type is side and i.status is InvoiceStatus.DRAFT]
        fill_from = max(drafts) + timedelta(days=1) if drafts else as_of + timedelta(days=1)
        for inv in rows:
            if inv.type is not side or inv.status is not InvoiceStatus.PAID:
                continue
            issued = inv.issued_on + SAME_WEEK_LAST_YEAR
            if not fill_from <= issued <= until:
                continue
            on = _cash_date(inv, inv.due_on + SAME_WEEK_LAST_YEAR, lateness)
            if on > until:
                continue
            flows.append(
                ExpectedFlow(
                    on=on,
                    amount=inv.gross_amount,
                    direction=Direction.IN if side is InvoiceType.RECEIVABLE else Direction.OUT,
                    confidence=Confidence.SPECULATIVE,
                    source_table="invoice",
                    source_id=inv.id,
                    label=(
                        f"Seasonal fill repeating {inv.id} from {inv.issued_on}, "
                        "the same week last year"
                    ),
                )
            )
    return flows


def projection_flows(
    as_of: date,
    weeks: int,
    invoices: Iterable[InvoiceLike],
    transactions: Iterable[TransactionLike],
    payroll_runs: Iterable[PayrollLike],
    obligations: Iterable[ObligationLike],
) -> list[ExpectedFlow]:
    """Every projected line, all three confidence classes, from ledger rows."""
    invoice_rows = list(invoices)
    lateness = mean_lateness_days(paid_receivable_history(invoice_rows, transactions))
    return [
        *expected_flows(invoice_rows, payroll_runs, obligations, lateness),
        *seasonal_fill(as_of, horizon_end(as_of, weeks), invoice_rows, lateness),
    ]
