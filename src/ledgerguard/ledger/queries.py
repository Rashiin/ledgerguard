"""Read-only ledger queries for the API and, later, the verifier's ledger snapshot."""

from __future__ import annotations

from collections import defaultdict
from datetime import date
from decimal import Decimal
from enum import Enum
from typing import Any

from sqlalchemy import inspect, select
from sqlalchemy.orm import Session

from ledgerguard.db.models import (
    Account,
    ActionIntentRecord,
    AuditEvent,
    Base,
    Business,
    Counterparty,
    FinancingProduct,
    Invoice,
    Obligation,
    PayrollRun,
    Transaction,
    TrustReceiptRecord,
)
from ledgerguard.domain.enums import AccountType, Direction, InvoiceStatus, PayrollStatus

ZERO = Decimal("0.00")

# Tables a client may fetch single rows from, by name. Everything is read-only.
ROW_TABLES: dict[str, type[Base]] = {
    "business": Business,
    "account": Account,
    "transaction": Transaction,
    "counterparty": Counterparty,
    "invoice": Invoice,
    "payroll_run": PayrollRun,
    "obligation": Obligation,
    "financing_product": FinancingProduct,
    "action_intent": ActionIntentRecord,
    "trust_receipt": TrustReceiptRecord,
    "audit_event": AuditEvent,
}


def jsonable(value: Any) -> Any:
    """Decimals become strings (never floats), dates ISO strings, enums their values."""
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, dict):
        return {k: jsonable(v) for k, v in value.items()}
    if isinstance(value, list | tuple):
        return [jsonable(v) for v in value]
    return value


def row_dict(row: Base) -> dict[str, Any]:
    return {attr.key: jsonable(getattr(row, attr.key)) for attr in inspect(row).mapper.column_attrs}


def get_row(session: Session, table: str, row_id: str) -> dict[str, Any] | None:
    model = ROW_TABLES.get(table)
    if model is None:
        raise KeyError(table)
    row = session.get(model, row_id)
    return None if row is None else row_dict(row)


def account_balances(session: Session, as_of: date) -> dict[str, Decimal]:
    balances = {a.id: a.opening_balance for a in session.scalars(select(Account))}
    for t in session.scalars(select(Transaction).where(Transaction.booked_on <= as_of)):
        balances[t.account_id] += t.amount if t.direction is Direction.IN else -t.amount
    return balances


def summary(session: Session) -> dict[str, Any]:
    business = session.scalars(select(Business)).one()
    as_of = business.data_as_of
    balances = account_balances(session, as_of)
    accounts = []
    cash = ZERO
    for acc in session.scalars(select(Account).order_by(Account.id)):
        entry: dict[str, Any] = {
            "id": acc.id,
            "name": acc.name,
            "type": acc.type.value,
            "balance": str(balances[acc.id]),
        }
        if acc.type is AccountType.CREDIT_LINE and acc.credit_limit is not None:
            entry["credit_limit"] = str(acc.credit_limit)
            entry["available"] = str(acc.credit_limit + balances[acc.id])
        else:
            cash += balances[acc.id]
        accounts.append(entry)

    open_items: dict[str, dict[str, dict[str, Any]]] = {
        "receivable": defaultdict(lambda: {"count": 0, "outstanding": ZERO}),
        "payable": defaultdict(lambda: {"count": 0, "outstanding": ZERO}),
    }
    for inv in session.scalars(select(Invoice).where(Invoice.status != InvoiceStatus.PAID)):
        bucket = open_items[inv.type.value][inv.status.value]
        bucket["count"] += 1
        bucket["outstanding"] += inv.gross_amount - inv.paid_amount

    next_payroll = session.scalars(
        select(PayrollRun)
        .where(PayrollRun.status == PayrollStatus.SCHEDULED)
        .order_by(PayrollRun.pay_date)
        .limit(1)
    ).first()
    return {
        "business": {
            "id": business.id,
            "legal_name": business.legal_name,
            "currency": business.currency,
            "data_as_of": as_of.isoformat(),
        },
        "cash_on_hand": str(cash),
        "accounts": accounts,
        "open_invoices": {
            side: {status: jsonable(v) for status, v in sorted(statuses.items())}
            for side, statuses in open_items.items()
        },
        "next_payroll": None
        if next_payroll is None
        else {
            "id": next_payroll.id,
            "pay_date": next_payroll.pay_date.isoformat(),
            "employer_cost_total": str(next_payroll.employer_cost_total),
        },
        "rows": {
            "transactions": len(session.scalars(select(Transaction.id)).all()),
            "invoices": len(session.scalars(select(Invoice.id)).all()),
        },
    }
