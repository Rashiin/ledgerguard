"""The business-rule engine (BUILD_SPEC 6.2, stage 3).

Each rule is a pure function of a ``RuleContext`` (the parsed intent, the
verifier's own resolution of what it would do, the ledger snapshot and the
policy). It returns PASS, WARN or BLOCK with a reason a business owner can
read. Rules never read the prompt, so text injected into the prompt cannot
reach them.

A BLOCK is absolute: the verifier will not seal a blocked action, and the
Executor refuses one even if it is somehow sealed.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal
from enum import StrEnum
from typing import Any

from ledgerguard.config import Policy
from ledgerguard.domain.enums import (
    AccountType,
    ActionKind,
    Direction,
    InvoiceStatus,
    ObligationKind,
)
from ledgerguard.money import format_eur
from ledgerguard.verifier.intent import ActionIntent
from ledgerguard.verifier.recompute import Resolution
from ledgerguard.verifier.snapshot import Band, LedgerSnapshot
from ledgerguard.verifier.tiers import OUTBOUND

ZERO = Decimal("0.00")


class Outcome(StrEnum):
    PASS = "PASS"
    WARN = "WARN"
    BLOCK = "BLOCK"


@dataclass(frozen=True)
class RuleResult:
    rule: str
    result: Outcome
    reason: str
    flags: tuple[str, ...] = ()
    rows: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        return {
            "rule": self.rule,
            "result": self.result.value,
            "reason": self.reason,
            "flags": list(self.flags),
            "rows": list(self.rows),
        }


@dataclass(frozen=True)
class RuleContext:
    intent: ActionIntent
    resolution: Resolution
    snapshot: LedgerSnapshot
    policy: Policy
    cited_rows: frozenset[str]  # rows cited by the intent's claims

    @property
    def band(self) -> tuple[Any, ...]:
        return Band.BY_NAME[self.policy.rules.protection_band]

    def cash_on(self, day: date, with_action: bool = True) -> Decimal:
        r = self.resolution
        if not with_action:
            return self.snapshot.projected_cash_on(day, self.band)
        return self.snapshot.projected_cash_on(day, self.band, r.outflows, r.replaces)


Rule = Callable[[RuleContext], RuleResult]


def _pass(name: str, reason: str) -> RuleResult:
    return RuleResult(name, Outcome.PASS, reason)


NO_OUTBOUND = "no outbound money in this action"


# --------------------------------------------------------------------------- rules


def sufficient_balance(ctx: RuleContext) -> RuleResult:
    name = "SufficientBalance"
    total = ctx.resolution.outbound_total
    if total <= 0:
        return _pass(name, NO_OUTBOUND)
    banks = sorted(
        (a for a in ctx.snapshot.accounts.values() if a.type is AccountType.BANK),
        key=lambda a: a.id,
    )
    if not banks:
        return RuleResult(name, Outcome.BLOCK, "there is no bank account to pay from")
    account = banks[0]
    balance = ctx.snapshot.balances[account.id]
    floor = ctx.policy.account_floors.get(account.id, ZERO)
    after = balance - total
    if after < floor:
        return RuleResult(
            name,
            Outcome.BLOCK,
            f"Paying {format_eur(total)} from {account.name} (balance {format_eur(balance)}) "
            f"would leave {format_eur(after)}, below its {format_eur(floor)} floor.",
            ("INSUFFICIENT_BALANCE",),
            (account.id,),
        )
    return _pass(
        name,
        f"{account.name} keeps {format_eur(after)} after this payment (floor {format_eur(floor)}).",
    )


def amount_ceiling(ctx: RuleContext) -> RuleResult:
    """Caps money leaving the business. Inbound records are bounded by the invoice instead."""
    name = "AmountCeiling"
    if ctx.intent.kind not in OUTBOUND or ctx.resolution.amount is None:
        return _pass(name, "no money leaves the business in this action")
    amount, ceiling = ctx.resolution.amount, ctx.policy.outbound_ceiling
    if amount > ceiling:
        return RuleResult(
            name,
            Outcome.BLOCK,
            f"{format_eur(amount)} is above the {format_eur(ceiling)} ceiling for money "
            "leaving the business. It cannot be approved here.",
            ("ABOVE_OUTBOUND_CEILING",),
        )
    return _pass(name, f"{format_eur(amount)} is within the {format_eur(ceiling)} ceiling.")


def duplicate_payment(ctx: RuleContext) -> RuleResult:
    name = "DuplicatePayment"
    payments = ctx.resolution.payments
    if not payments:
        return _pass(name, NO_OUTBOUND)
    window = ctx.policy.rules.duplicate_payment_window_days
    pay_on = ctx.resolution.pay_on or ctx.snapshot.as_of
    findings: list[str] = []
    rows: list[str] = []
    for i, pay in enumerate(payments):
        for txn in ctx.snapshot.transactions.values():
            if (
                txn.direction is Direction.OUT
                and txn.counterparty_id == pay.counterparty_id
                and txn.amount == pay.amount
                and 0 <= (pay_on - txn.booked_on).days <= window
            ):
                settled = f" (settling {txn.invoice_id})" if txn.invoice_id else ""
                findings.append(
                    f"{pay.invoice_id}: {format_eur(pay.amount)} to the same supplier was "
                    f"already paid on {txn.booked_on}{settled}, "
                    f"{(pay_on - txn.booked_on).days} days earlier."
                )
                rows += [pay.invoice_id, txn.id, *([txn.invoice_id] if txn.invoice_id else [])]
        for other in payments[i + 1 :]:
            if other.counterparty_id == pay.counterparty_id and other.amount == pay.amount:
                findings.append(
                    f"{pay.invoice_id} and {other.invoice_id} are both "
                    f"{format_eur(pay.amount)} to the same supplier."
                )
                rows += [pay.invoice_id, other.invoice_id]
    if findings:
        return RuleResult(
            name,
            Outcome.WARN,
            "Possible duplicate payment. " + " ".join(findings) + " This needs a human decision.",
            ("DUPLICATE_PAYMENT",),
            tuple(dict.fromkeys(rows)),
        )
    return _pass(
        name, f"no payment of the same amount to the same supplier in the last {window} days"
    )


def counterparty_whitelist(ctx: RuleContext) -> RuleResult:
    name = "CounterpartyWhitelist"
    payments = ctx.resolution.payments
    if not payments:
        return _pass(name, NO_OUTBOUND)
    blocked = sorted(
        {
            p.counterparty_id
            for p in payments
            if not ctx.snapshot.counterparties[p.counterparty_id].is_whitelisted
        }
    )
    if blocked:
        names = ", ".join(f"{ctx.snapshot.counterparties[c].name} ({c})" for c in blocked)
        return RuleResult(
            name,
            Outcome.BLOCK,
            f"Outbound money may only go to whitelisted counterparties; not whitelisted: {names}.",
            ("COUNTERPARTY_NOT_WHITELISTED",),
            tuple(blocked),
        )
    return _pass(name, "every payee is whitelisted")


def payroll_protection(ctx: RuleContext) -> RuleResult:
    """No outbound action may leave less projected cash than the next payroll needs.

    The cash must be in place ``payroll_lead_days`` before pay day, judged on
    the policy's protection band.
    """
    name = "PayrollProtection"
    snap, lead = ctx.snapshot, ctx.policy.rules.payroll_lead_days
    run = snap.next_payroll_after(snap.as_of)
    if run is None:
        return _pass(name, "no scheduled payroll in the horizon")
    need = run.employer_cost_total
    check_from = max(run.pay_date - timedelta(days=lead), snap.as_of)
    days = [check_from, run.pay_date - timedelta(days=1)]
    after = min(ctx.cash_on(d) for d in days)
    pay_on = ctx.resolution.pay_on
    outbound = ctx.resolution.outbound_total > 0 and pay_on is not None and pay_on < run.pay_date
    where = f"payroll {run.id} on {run.pay_date} ({format_eur(need)}, needed {lead} days ahead)"
    if outbound and after < need:
        return RuleResult(
            name,
            Outcome.BLOCK,
            f"Paying {format_eur(ctx.resolution.outbound_total)} now would leave projected "
            f"cash of {format_eur(after)} before {where}: short by {format_eur(need - after)}.",
            ("PAYROLL_PROTECTION",),
            (run.id,),
        )
    margin = after - need
    if margin < need * ctx.policy.rules.payroll_margin_warning:
        detail = (
            f"falls below the requirement by {format_eur(-margin)}"
            if margin < 0
            else f"covers it with only {format_eur(margin)} to spare"
        )
        return RuleResult(
            name,
            Outcome.WARN,
            f"Projected cash before {where} {detail}.",
            ("PAYROLL_PRESSURE",),
            (run.id,),
        )
    return _pass(name, f"projected cash covers {where} with {format_eur(margin)} to spare")


def tax_reserve_floor(ctx: RuleContext) -> RuleResult:
    """Tax due within the horizon is ring-fenced: cash on the eve of each due date covers it."""
    name = "TaxReserveFloor"
    snap = ctx.snapshot
    until = snap.as_of + timedelta(days=ctx.policy.rules.tax_reserve_horizon_days)
    taxes = sorted(
        (
            o
            for o in snap.obligations.values()
            if o.kind is ObligationKind.TAX and snap.as_of < o.due_on <= until
        ),
        key=lambda o: o.due_on,
    )
    if not taxes:
        return _pass(name, "no tax due inside the reserve horizon")
    outbound = ctx.resolution.outbound_total > 0
    for tax in taxes:
        eve = tax.due_on - timedelta(days=1)
        after = ctx.cash_on(eve)
        if after < tax.amount:
            text = (
                f"{tax.description} ({format_eur(tax.amount)}, due {tax.due_on}) is "
                f"ring-fenced; projected cash the day before would be "
                f"{format_eur(after)}."
            )
            if outbound:
                return RuleResult(name, Outcome.BLOCK, text, ("TAX_RESERVE",), (tax.id,))
            return RuleResult(name, Outcome.WARN, text, ("TAX_RESERVE_PRESSURE",), (tax.id,))
    return _pass(name, f"cash covers {len(taxes)} tax payment(s) due by {until}")


def disputed_invoice_lock(ctx: RuleContext) -> RuleResult:
    name = "DisputedInvoiceLock"
    locked = [
        i
        for i in ctx.resolution.settles
        if ctx.snapshot.invoices[i].status is InvoiceStatus.DISPUTED
    ]
    if locked:
        return RuleResult(
            name,
            Outcome.BLOCK,
            f"{', '.join(locked)} is disputed; no settlement action is allowed "
            "until the dispute is resolved.",
            ("DISPUTED_INVOICE",),
            tuple(locked),
        )
    return _pass(name, "no disputed invoice is being settled")


def concentration_warning(ctx: RuleContext) -> RuleResult:
    """Advisory only. Never blocks."""
    name = "ConcentrationWarning"
    snap = ctx.snapshot
    shares = snap.revenue_shares()
    involved = set(ctx.resolution.counterparties)
    for row_id in ctx.cited_rows:
        row = snap.row(row_id)
        cp = getattr(row, "counterparty_id", None) or (
            row_id if row_id in snap.counterparties else None
        )
        if cp:
            involved.add(cp)
    if (
        ctx.intent.kind
        in (ActionKind.DRAFT_FORECAST, ActionKind.RANK_FINANCING, ActionKind.RUN_SCENARIO)
        and shares
    ):
        involved.add(max(shares, key=lambda c: shares[c]))
    threshold = ctx.policy.rules.concentration_warning_share
    heavy = sorted(c for c in involved if shares.get(c, ZERO) > threshold)
    if heavy:
        parts = [
            f"{snap.counterparties[c].name} is {(shares[c] * 100):.1f}% of trailing "
            "12-month revenue"
            for c in heavy
        ]
        return RuleResult(
            name,
            Outcome.WARN,
            "; ".join(parts) + f" (above {threshold * 100:.0f}%). Advisory.",
            ("CONCENTRATION_RISK",),
            tuple(heavy),
        )
    return _pass(name, "no concentrated customer involved")


REGISTRY: tuple[Rule, ...] = (
    sufficient_balance,
    amount_ceiling,
    duplicate_payment,
    counterparty_whitelist,
    payroll_protection,
    tax_reserve_floor,
    disputed_invoice_lock,
    concentration_warning,
)
ADVISORY = frozenset({"ConcentrationWarning"})  # may WARN, can never BLOCK
NEVER_AUTO_PASS = frozenset({"DUPLICATE_PAYMENT"})  # flags that force human approval


def evaluate(ctx: RuleContext) -> list[RuleResult]:
    results = []
    for rule in REGISTRY:
        result = rule(ctx)
        if result.rule in ADVISORY and result.result is Outcome.BLOCK:
            raise AssertionError(f"{result.rule} is advisory and must not block")
        results.append(result)
    return results
