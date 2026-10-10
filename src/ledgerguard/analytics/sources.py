"""Turn ledger rows into projection inputs.

Works on any objects with the model attributes (ORM rows or unsaved
instances), so the seeder and the API share exactly one definition of what
an "expected flow" is.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from datetime import date
from decimal import Decimal
from typing import Protocol

from ledgerguard.analytics.cashflow import ExpectedFlow, PaidInvoice, expected_receipt_date
from ledgerguard.domain.enums import (
    Direction,
    InvoiceStatus,
    InvoiceType,
    PayrollStatus,
    TransactionSource,
)

OPEN_RECEIVABLE = {
    InvoiceStatus.DRAFT,
    InvoiceStatus.SENT,
    InvoiceStatus.PART_PAID,
    InvoiceStatus.OVERDUE,
}
OPEN_PAYABLE = {
    InvoiceStatus.DRAFT,
    InvoiceStatus.SENT,
    InvoiceStatus.PART_PAID,
    InvoiceStatus.OVERDUE,
}


class InvoiceLike(Protocol):
    id: str
    type: InvoiceType
    counterparty_id: str
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


def expected_flows(
    invoices: Iterable[InvoiceLike],
    payroll_runs: Iterable[PayrollLike],
    obligations: Iterable[ObligationLike],
    lateness: Mapping[str, int],
) -> list[ExpectedFlow]:
    flows: list[ExpectedFlow] = []
    for inv in invoices:
        outstanding = inv.gross_amount - inv.paid_amount
        if outstanding <= 0:
            continue
        if inv.type is InvoiceType.RECEIVABLE and inv.status in OPEN_RECEIVABLE:
            late = lateness.get(inv.counterparty_id, 0)
            flows.append(
                ExpectedFlow(
                    on=expected_receipt_date(inv.due_on, late),
                    amount=outstanding,
                    direction=Direction.IN,
                    source_table="invoice",
                    source_id=inv.id,
                    label=f"Receivable {inv.id} (due {inv.due_on}, +{late}d typical lateness)",
                )
            )
        elif inv.type is InvoiceType.PAYABLE and inv.status in OPEN_PAYABLE:
            flows.append(
                ExpectedFlow(
                    on=inv.due_on,
                    amount=outstanding,
                    direction=Direction.OUT,
                    source_table="invoice",
                    source_id=inv.id,
                    label=f"Payable {inv.id} (due {inv.due_on})",
                )
            )
    for run in payroll_runs:
        if run.status is PayrollStatus.SCHEDULED:
            flows.append(
                ExpectedFlow(
                    on=run.pay_date,
                    amount=run.employer_cost_total,
                    direction=Direction.OUT,
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
                source_table="obligation",
                source_id=ob.id,
                label=ob.description,
            )
        )
    return flows
