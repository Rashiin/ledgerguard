"""Stage 2: recompute every figure from source rows.

This stage has two jobs.

* **Claims.** Each claimed figure is derived again with its declared formula
  over its cited rows, in plain Python. The recomputed value always wins. A
  figure that cannot be recomputed is not shown to the user at all. That
  covers a missing derivation, unknown rows, rows of the wrong kind and bad
  parameters.
* **Payloads.** The amounts an action would move come from the ledger, such
  as what an invoice still owes, and never from the model. Every reference in
  the payload must name an existing row of the right kind, or the intent is
  rejected.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from ledgerguard.analytics.cashflow import ExpectedFlow
from ledgerguard.db.models import (
    Account,
    Base,
    Counterparty,
    Invoice,
    Transaction,
)
from ledgerguard.domain.enums import (
    AccountType,
    Confidence,
    CounterpartyType,
    Direction,
    InvoiceStatus,
    InvoiceType,
)
from ledgerguard.money import CENT
from ledgerguard.verifier import intent as I
from ledgerguard.verifier.snapshot import LedgerSnapshot, table_of

ZERO = Decimal("0.00")
PERCENT = Decimal("0.01")

MONEY_FIELDS: dict[str, frozenset[str]] = {
    "invoice": frozenset({"gross_amount", "net_amount", "tax_amount", "paid_amount"}),
    "transaction": frozenset({"amount"}),
    "payroll_run": frozenset({"gross_total", "employer_cost_total"}),
    "obligation": frozenset({"amount"}),
    "account": frozenset({"opening_balance", "credit_limit"}),
    "financing_product": frozenset({"min_amount", "max_amount"}),
}
PROJECTION_TABLES = frozenset({"account", "invoice", "payroll_run", "obligation", "transaction"})


class FormulaError(Exception):
    pass


@dataclass(frozen=True)
class FormulaSpec:
    tables: frozenset[str]  # operand rows must come from these tables
    text: str  # shown on the Trust Receipt
    compute: Callable[[LedgerSnapshot, list[Base], dict[str, str]], Decimal]
    exactly_one: bool = False


def _week(params: dict[str, str]) -> int:
    try:
        week = int(params["week"])
    except (KeyError, ValueError) as exc:
        raise FormulaError("params.week must be a week number 1-13") from exc
    if not 1 <= week <= 13:
        raise FormulaError("params.week must be 1-13")
    return week


def _row_field(snap: LedgerSnapshot, rows: list[Base], params: dict[str, str]) -> Decimal:
    row = rows[0]
    name = params.get("field", "")
    if name not in MONEY_FIELDS.get(row.__tablename__, frozenset()):
        raise FormulaError(f"{row.__tablename__} has no monetary field {name!r}")
    value = getattr(row, name)
    if value is None:
        raise FormulaError(f"{name} is empty")
    return Decimal(value)


def _outstanding(snap: LedgerSnapshot, rows: list[Base], params: dict[str, str]) -> Decimal:
    return sum((r.gross_amount - r.paid_amount for r in rows if isinstance(r, Invoice)), ZERO)


def _sum_txns(snap: LedgerSnapshot, rows: list[Base], params: dict[str, str]) -> Decimal:
    return sum((r.amount for r in rows if isinstance(r, Transaction)), ZERO)


def _balance(snap: LedgerSnapshot, rows: list[Base], params: dict[str, str]) -> Decimal:
    return snap.balances[rows[0].id]  # type: ignore[attr-defined]


def _cash(snap: LedgerSnapshot, rows: list[Base], params: dict[str, str]) -> Decimal:
    accounts = [r for r in rows if isinstance(r, Account)]
    if any(a.type is AccountType.CREDIT_LINE for a in accounts):
        raise FormulaError("a credit line is headroom, not cash on hand")
    return sum((snap.balances[a.id] for a in accounts), ZERO)


def _closing(snap: LedgerSnapshot, rows: list[Base], params: dict[str, str]) -> Decimal:
    week = snap.weeks[_week(params) - 1]
    band = params.get("band", "all")
    if band == "contracted":
        return week.closing_contracted
    if band == "with_expected":
        return week.closing_with_expected
    if band == "all":
        return week.closing
    raise FormulaError("params.band must be contracted, with_expected or all")


def _class_total(direction: Direction) -> Callable[..., Decimal]:
    def compute(snap: LedgerSnapshot, rows: list[Base], params: dict[str, str]) -> Decimal:
        week = snap.weeks[_week(params) - 1]
        totals = week.inflow if direction is Direction.IN else week.outflow
        name = params.get("confidence", "all")
        if name == "all":
            return sum(totals.values(), ZERO)
        try:
            return totals[Confidence(name)]
        except ValueError as exc:
            raise FormulaError("params.confidence must be a class name or all") from exc

    return compute


def _share(snap: LedgerSnapshot, rows: list[Base], params: dict[str, str]) -> Decimal:
    share = snap.revenue_shares().get(rows[0].id, ZERO)  # type: ignore[attr-defined]
    return (share * 100).quantize(PERCENT, rounding=ROUND_HALF_UP)


FORMULAS: dict[I.Formula, FormulaSpec] = {
    I.Formula.ROW_FIELD: FormulaSpec(frozenset(MONEY_FIELDS), "row[field]", _row_field, True),
    I.Formula.INVOICE_OUTSTANDING: FormulaSpec(
        frozenset({"invoice"}), "Σ (gross − paid)", _outstanding
    ),
    I.Formula.SUM_TRANSACTIONS: FormulaSpec(frozenset({"transaction"}), "Σ amount", _sum_txns),
    I.Formula.ACCOUNT_BALANCE: FormulaSpec(
        frozenset({"account"}), "opening + Σin − Σout to as-of date", _balance, True
    ),
    I.Formula.CASH_ON_HAND: FormulaSpec(frozenset({"account"}), "Σ bank and cash balances", _cash),
    I.Formula.PROJECTED_CLOSING: FormulaSpec(
        PROJECTION_TABLES, "opening + Σin − Σout for the chosen band", _closing
    ),
    I.Formula.PROJECTED_INFLOW: FormulaSpec(
        PROJECTION_TABLES, "Σ expected inflows in week", _class_total(Direction.IN)
    ),
    I.Formula.PROJECTED_OUTFLOW: FormulaSpec(
        PROJECTION_TABLES, "Σ expected outflows in week", _class_total(Direction.OUT)
    ),
    I.Formula.REVENUE_SHARE: FormulaSpec(
        frozenset({"counterparty"}),
        "customer net revenue ÷ total, trailing 12 months, %",
        _share,
        True,
    ),
}


# --------------------------------------------------------------------------- claims


@dataclass(frozen=True)
class Recomputation:
    claim_index: int
    claim: str
    status: str  # match | mismatch | not_recomputable | no_figure
    model_figure: Decimal | None
    recomputed: Decimal | None
    formula: str | None
    operands: tuple[str, ...]
    reason: str | None = None

    @property
    def is_percent(self) -> bool:
        return self.formula == FORMULAS[I.Formula.REVENUE_SHARE].text

    def as_dict(self) -> dict[str, Any]:
        return {
            "claim_index": self.claim_index,
            "claim": self.claim,
            "status": self.status,
            "model_figure": None if self.model_figure is None else str(self.model_figure),
            "recomputed": None if self.recomputed is None else str(self.recomputed),
            "match": self.status == "match",
            "formula": self.formula,
            "operands": list(self.operands),
            "reason": self.reason,
        }


def recompute_claim(
    snap: LedgerSnapshot, index: int, claim: I.Claim, reads: set[str]
) -> Recomputation:
    if claim.figure is None:
        return Recomputation(index, claim.statement, "no_figure", None, None, None, ())
    d = claim.derivation
    if d is None:
        return Recomputation(
            index,
            claim.statement,
            "not_recomputable",
            claim.figure,
            None,
            None,
            (),
            "figure has no derivation, so it cannot be checked",
        )
    spec = FORMULAS[d.formula]
    operands = tuple(d.operands)

    def fail(reason: str) -> Recomputation:
        return Recomputation(
            index,
            claim.statement,
            "not_recomputable",
            claim.figure,
            None,
            spec.text,
            operands,
            reason,
        )

    rows: list[Base] = []
    for row_id in operands:
        row = snap.row(row_id)
        if row is None:
            return fail(f"operand {row_id} does not exist")
        if table_of(row_id) not in spec.tables:
            return fail(
                f"{row_id} is a {table_of(row_id)} row; {d.formula.value} "
                f"takes {', '.join(sorted(spec.tables))}"
            )
        rows.append(row)
        reads.add(row_id)
    if spec.exactly_one and len(rows) != 1:
        return fail(f"{d.formula.value} takes exactly one operand")
    try:
        value = spec.compute(snap, rows, d.params)
    except FormulaError as exc:
        return fail(str(exc))
    if d.formula is not I.Formula.REVENUE_SHARE:
        value = value.quantize(CENT, rounding=ROUND_HALF_UP)
    status = "match" if value == claim.figure else "mismatch"
    return Recomputation(index, claim.statement, status, claim.figure, value, spec.text, operands)


# --------------------------------------------------------------------------- payloads


@dataclass(frozen=True)
class Payment:
    invoice_id: str
    counterparty_id: str
    amount: Decimal


@dataclass
class Resolution:
    """What the action would do, in ledger terms, computed by the verifier."""

    amount: Decimal | None = None  # magnitude used for ceilings and tiers
    payments: list[Payment] = field(default_factory=list)  # outbound money
    pay_on: date | None = None
    outflows: list[ExpectedFlow] = field(default_factory=list)
    replaces: frozenset[str] = frozenset()  # projected flows the action supersedes
    settles: list[str] = field(default_factory=list)  # invoices a settlement touches
    counterparties: set[str] = field(default_factory=set)
    errors: list[str] = field(default_factory=list)
    figures: dict[str, Decimal] = field(default_factory=dict)  # verified payload figures

    @property
    def outbound_total(self) -> Decimal:
        return sum((p.amount for p in self.payments), ZERO)


def _invoice(
    snap: LedgerSnapshot, invoice_id: str, side: InvoiceType, res: Resolution, reads: set[str]
) -> Invoice | None:
    inv = snap.invoices.get(invoice_id)
    if inv is None:
        res.errors.append(f"payload references {invoice_id}, which does not exist")
        return None
    reads.add(invoice_id)
    if inv.type is not side:
        res.errors.append(f"{invoice_id} is a {inv.type.value}, not a {side.value}")
        return None
    res.counterparties.add(inv.counterparty_id)
    return inv


def _counterparty(
    snap: LedgerSnapshot,
    cp_id: str,
    kind: CounterpartyType | None,
    res: Resolution,
    reads: set[str],
) -> Counterparty | None:
    cp = snap.counterparties.get(cp_id)
    if cp is None:
        res.errors.append(f"payload references {cp_id}, which does not exist")
        return None
    reads.add(cp_id)
    if kind is not None and cp.type is not kind:
        res.errors.append(f"{cp_id} is a {cp.type.value}, not a {kind.value}")
        return None
    res.counterparties.add(cp_id)
    return cp


def resolve_payload(snap: LedgerSnapshot, intent: I.ActionIntent, reads: set[str]) -> Resolution:
    res = Resolution()
    p = intent.payload
    if isinstance(p, I.InitiatePayment):
        if len(set(p.invoice_ids)) != len(p.invoice_ids):
            res.errors.append("the same invoice is listed twice")
        res.pay_on = p.pay_on or snap.as_of
        if res.pay_on < snap.as_of:
            res.errors.append("a payment cannot be dated before the ledger date")
        for invoice_id in dict.fromkeys(p.invoice_ids):
            inv = _invoice(snap, invoice_id, InvoiceType.PAYABLE, res, reads)
            if inv is None:
                continue
            if inv.status in (InvoiceStatus.PAID, InvoiceStatus.DRAFT):
                res.errors.append(f"{invoice_id} is {inv.status.value}; there is nothing to pay")
                continue
            owed = inv.gross_amount - inv.paid_amount
            res.payments.append(Payment(invoice_id, inv.counterparty_id, owed))
            res.settles.append(invoice_id)
        res.outflows = [
            ExpectedFlow(
                res.pay_on,
                pay.amount,
                Direction.OUT,
                Confidence.CONTRACTED,
                "invoice",
                pay.invoice_id,
                f"Proposed payment of {pay.invoice_id}",
            )
            for pay in res.payments
        ]
        res.replaces = frozenset(pay.invoice_id for pay in res.payments)
        res.amount = res.outbound_total
        res.figures["payment_total"] = res.outbound_total
    elif isinstance(p, I.RecordPayment):
        inv = _invoice(snap, p.invoice_id, InvoiceType.RECEIVABLE, res, reads)
        if inv is not None:
            owed = inv.gross_amount - inv.paid_amount
            amount = owed if p.amount is None else p.amount
            if owed <= 0:
                res.errors.append(f"{p.invoice_id} has nothing outstanding")
            elif amount <= 0 or amount > owed:
                res.errors.append(
                    f"recorded amount must be between 0.01 and the outstanding {owed}"
                )
            res.amount = amount
            res.settles.append(p.invoice_id)
            res.figures["recorded_amount"] = amount
            res.figures["outstanding_before"] = owed
            if p.received_on and p.received_on > snap.as_of:
                res.errors.append("a receipt cannot be dated after the ledger date")
    elif isinstance(p, I.MarkDisputed):
        _invoice(snap, p.invoice_id, InvoiceType.RECEIVABLE, res, reads)
    elif isinstance(p, I.AcceptFinancingOffer):
        product = snap.financing_products.get(p.product_id)
        if product is None:
            res.errors.append(f"payload references {p.product_id}, which does not exist")
        else:
            reads.add(p.product_id)
            if not product.min_amount <= p.amount <= product.max_amount:
                res.errors.append(
                    f"{p.product_id} lends {product.min_amount} to {product.max_amount}"
                )
        res.amount = p.amount
        res.figures["financing_amount"] = p.amount
    elif isinstance(p, I.DraftInvoice):
        _counterparty(snap, p.counterparty_id, CounterpartyType.CUSTOMER, res, reads)
        res.amount = p.net_amount
        res.figures["net_amount"] = p.net_amount
    elif isinstance(p, I.FlagAnomaly):
        for txn_id in p.transaction_ids:
            if txn_id not in snap.transactions:
                res.errors.append(f"payload references {txn_id}, which does not exist")
            else:
                reads.add(txn_id)
    elif isinstance(p, I.RunScenario):
        s = p.scenario
        if isinstance(s, I.CustomerPaysLate):
            _counterparty(snap, s.counterparty_id, CounterpartyType.CUSTOMER, res, reads)
        elif isinstance(s, I.DelayPayable):
            _invoice(snap, s.invoice_id, InvoiceType.PAYABLE, res, reads)
        elif isinstance(s, I.DrawCreditLine):
            lines = [a for a in snap.accounts.values() if a.type is AccountType.CREDIT_LINE]
            headroom = sum((a.credit_limit or ZERO) + snap.balances[a.id] for a in lines)
            for a in lines:
                reads.add(a.id)
            if s.amount > headroom:
                res.errors.append(f"the credit line has {headroom} available")
            res.figures["draw_amount"] = s.amount
    return res
