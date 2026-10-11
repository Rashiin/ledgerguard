"""Financing Matcher: which products could cover the projected gap, and what each really costs.

There is no model in here. The gap comes from the projection. Eligibility is a
hard filter on the product's own rules. Every eligible product's total cost of
capital is computed in euros over the real drawdown period, or over the full
term where a product cannot be repaid early. Options are then ranked on a
weighted sum of five scores that are all shown, and the owner can change the
weights. A "do nothing" row is always included.

The catalogue is synthetic. No provider, rate or fee in it is a real offer.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from ledgerguard.db.models import FinancingProduct
from ledgerguard.domain.enums import (
    FinancingKind,
    InvoiceStatus,
    InvoiceType,
)
from ledgerguard.money import CENT, format_eur
from ledgerguard.verifier.snapshot import LedgerSnapshot

ZERO = Decimal("0.00")
HUNDRED = Decimal(100)
GAP_STEP = Decimal(1000)
DEFAULT_WEIGHTS: dict[str, Decimal] = {
    "cost": Decimal("0.40"),
    "coverage": Decimal("0.20"),
    "speed": Decimal("0.15"),
    "flexibility": Decimal("0.15"),
    "collateral": Decimal("0.10"),
}
FLEXIBILITY = {
    FinancingKind.CREDIT_LINE: Decimal(100),
    FinancingKind.INVOICE_FACTORING: Decimal(70),
    FinancingKind.SUPPLIER_CREDIT: Decimal(60),
    FinancingKind.MERCHANT_ADVANCE: Decimal(30),
    FinancingKind.TERM_LOAN: Decimal(20),
}


@dataclass(frozen=True)
class Gap:
    amount: Decimal  # the deepest projected shortfall, rounded up to a whole €1,000
    need_by: date  # the start of the first week below zero
    until: date  # the end of the last week below zero
    lead_days: int

    @property
    def days(self) -> int:
        return (self.until - self.need_by).days + 1


@dataclass
class Option:
    product_id: str
    provider: str
    name: str
    kind: FinancingKind
    eligible: bool
    reasons: list[str] = field(default_factory=list)
    amount: Decimal = ZERO
    days: int = 0
    full_term_months: int | None = None  # set when the cost runs past the gap
    total_cost: Decimal = ZERO
    breakdown: list[tuple[str, Decimal]] = field(default_factory=list)
    funded_in_days: int = 0
    in_time: bool = False
    scores: dict[str, Decimal] = field(default_factory=dict)
    score: Decimal | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "product_id": self.product_id,
            "provider": self.provider,
            "name": self.name,
            "kind": self.kind.value,
            "eligible": self.eligible,
            "reasons": self.reasons,
            "amount": str(self.amount),
            "days": self.days,
            "full_term_months": self.full_term_months,
            "total_cost": str(self.total_cost),
            "breakdown": [{"item": k, "amount": str(v)} for k, v in self.breakdown],
            "funded_in_days": self.funded_in_days,
            "in_time": self.in_time,
            "scores": {k: str(v) for k, v in self.scores.items()},
            "score": None if self.score is None else str(self.score),
        }


@dataclass(frozen=True)
class Match:
    gap: Gap | None
    options: list[Option]
    weights: dict[str, Decimal]
    do_nothing: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "synthetic_catalogue": True,
            "gap": None
            if self.gap is None
            else {
                "amount": str(self.gap.amount),
                "need_by": str(self.gap.need_by),
                "until": str(self.gap.until),
                "days": self.gap.days,
                "lead_days": self.gap.lead_days,
            },
            "weights": {k: str(v) for k, v in self.weights.items()},
            "options": [o.as_dict() for o in self.options],
            "do_nothing": {"total_cost": "0.00", "consequence": self.do_nothing},
        }


def find_gap(snap: LedgerSnapshot) -> Gap | None:
    negative = [w for w in snap.weeks if w.closing < 0]
    if not negative:
        return None
    worst = min(w.closing for w in negative)
    amount = (-worst / GAP_STEP).to_integral_value(rounding="ROUND_CEILING") * GAP_STEP
    return Gap(
        amount.quantize(CENT),
        negative[0].starts_on,
        negative[-1].ends_on,
        (negative[0].starts_on - snap.as_of).days,
    )


# --------------------------------------------------------------------------- eligibility


def _trailing_revenue(snap: LedgerSnapshot) -> Decimal:
    since = snap.as_of - timedelta(days=365)
    return sum(
        (
            i.net_amount
            for i in snap.invoices.values()
            if i.type is InvoiceType.RECEIVABLE and since < i.issued_on <= snap.as_of
        ),
        ZERO,
    )


def factorable(snap: LedgerSnapshot, bands: list[str]) -> list[tuple[str, Decimal]]:
    """Issued, undisputed receivables from debtors in the given risk bands, largest first."""
    rows = []
    for inv in snap.invoices.values():
        if (
            inv.type is InvoiceType.RECEIVABLE
            and inv.status in (InvoiceStatus.SENT, InvoiceStatus.OVERDUE)
            and snap.counterparties[inv.counterparty_id].risk_band.value in bands
        ):
            rows.append((inv.id, inv.gross_amount - inv.paid_amount))
    return sorted(rows, key=lambda r: (-r[1], r[0]))


def _limit(snap: LedgerSnapshot, product: FinancingProduct, gap: Gap) -> tuple[Decimal, list[str]]:
    """How much this product could lend against this gap, and why not, if nothing."""
    rules = product.eligibility_rules
    reasons: list[str] = []
    years = (snap.as_of - snap.business.founded_on).days // 365
    if years < int(rules.get("min_trading_years", 0)):
        reasons.append(
            f"needs {rules['min_trading_years']} years of trading, the business has {years}"
        )
    if "min_annual_revenue" in rules and _trailing_revenue(snap) < Decimal(
        rules["min_annual_revenue"]
    ):
        reasons.append(
            f"needs annual revenue of {format_eur(Decimal(rules['min_annual_revenue']))}"
        )
    if snap.business.employee_count > int(rules.get("max_employees", 10**6)):
        reasons.append(f"lends only to businesses with up to {rules['max_employees']} employees")
    if "purposes" in rules and "working_capital" not in rules["purposes"]:
        reasons.append("does not lend for working capital")
    if rules.get("supplier_must_enrol"):
        reasons.append("needs the supplier to enrol in the programme, which is not confirmed")
    cap = min(gap.amount, product.max_amount)
    if "existing_facility_account" in rules:
        account = snap.accounts[rules["existing_facility_account"]]
        headroom = (account.credit_limit or ZERO) + snap.balances[account.id]
        cap = min(cap, headroom)
    if rules.get("requires_open_receivables"):
        rate = Decimal(product.fee_structure.get("advance_rate", "1"))
        face = sum(
            (
                v
                for _, v in factorable(
                    snap, rules.get("eligible_debtor_risk_bands", ["A", "B", "C"])
                )
            ),
            ZERO,
        )
        cap = min(cap, (face * rate).quantize(CENT))
    if rules.get("requires_open_payables"):
        owed = sum(
            (
                i.gross_amount - i.paid_amount
                for i in snap.invoices.values()
                if i.type is InvoiceType.PAYABLE
                and i.status in (InvoiceStatus.SENT, InvoiceStatus.OVERDUE)
            ),
            ZERO,
        )
        cap = min(cap, owed)
    if cap < product.min_amount:
        reasons.append(
            f"lends at least {format_eur(product.min_amount)}, and it can cover only "
            f"{format_eur(max(cap, ZERO))} here"
        )
    return (ZERO if reasons else cap), reasons


# --------------------------------------------------------------------------- cost


def _money(value: Decimal) -> Decimal:
    return value.quantize(CENT, rounding=ROUND_HALF_UP)


def total_cost(
    snap: LedgerSnapshot, product: FinancingProduct, amount: Decimal, days: int
) -> tuple[Decimal, list[tuple[str, Decimal]], int | None]:
    """Total cost of capital in euros, its parts, and the term in months if it outlasts the gap."""
    fees = product.fee_structure
    rate = product.nominal_rate
    parts: list[tuple[str, Decimal]] = []
    term: int | None = None
    periods = math.ceil(days / 30)
    if product.kind is FinancingKind.CREDIT_LINE:
        parts.append(("interest", _money(amount * rate * days / 365)))
        if "arrangement_fee" in fees:
            parts.append(("arrangement fee", Decimal(fees["arrangement_fee"])))
        if "drawdown_fee_rate" in fees:
            parts.append(("drawdown fee", _money(amount * Decimal(fees["drawdown_fee_rate"]))))
        if "non_utilisation_fee_annual" in fees:
            account = snap.accounts.get(
                product.eligibility_rules.get("existing_facility_account", "")
            )
            limit = account.credit_limit if account and account.credit_limit else amount
            undrawn = max(limit - amount, ZERO)
            parts.append(
                (
                    "fee on the undrawn limit",
                    _money(undrawn * Decimal(fees["non_utilisation_fee_annual"]) * days / 365),
                )
            )
    elif product.kind is FinancingKind.TERM_LOAN:
        term = product.term_months
        monthly = rate / 12
        if monthly == 0:
            interest = ZERO
        else:
            payment = amount * monthly / (1 - (1 + monthly) ** -term)
            interest = _money(payment * term - amount)
        parts.append((f"interest over the full {term}-month term", interest))
        fee = ZERO
        if "arrangement_fee_rate" in fees:
            fee = amount * Decimal(fees["arrangement_fee_rate"])
        if "arrangement_fee_min" in fees:
            fee = max(fee, Decimal(fees["arrangement_fee_min"]))
        if "arrangement_fee" in fees:
            fee = Decimal(fees["arrangement_fee"])
        parts.append(("arrangement fee", _money(fee)))
    elif product.kind is FinancingKind.INVOICE_FACTORING:
        advance = Decimal(fees["advance_rate"])
        needed_face = amount / advance
        chosen: list[tuple[str, Decimal]] = []
        face = ZERO
        for inv_id, owed in factorable(
            snap, product.eligibility_rules.get("eligible_debtor_risk_bands", ["A", "B", "C"])
        ):
            if face >= needed_face:
                break
            chosen.append((inv_id, owed))
            face += owed
        parts.append(
            (
                "discount fee",
                _money(amount * Decimal(fees["discount_fee_rate_per_30_days"]) * periods),
            )
        )
        service = face * Decimal(fees.get("service_fee_rate", "0"))
        if "min_fee_per_invoice" in fees:
            service = max(service, Decimal(fees["min_fee_per_invoice"]) * len(chosen))
        parts.append((f"service fee on {len(chosen)} invoices", _money(service)))
        if "minimum_annual_fee" in fees:
            term = product.term_months
            shortfall = Decimal(fees["minimum_annual_fee"]) - sum((v for _, v in parts), ZERO)
            if shortfall > 0:
                parts.append(("top-up to the minimum annual fee", _money(shortfall)))
    elif product.kind is FinancingKind.SUPPLIER_CREDIT:
        if "extension_fee_rate" in fees:
            parts.append(("extension fee", _money(amount * Decimal(fees["extension_fee_rate"]))))
        if "discount_fee_rate_per_30_days" in fees:
            parts.append(
                (
                    "discount fee",
                    _money(amount * Decimal(fees["discount_fee_rate_per_30_days"]) * periods),
                )
            )
    elif product.kind is FinancingKind.MERCHANT_ADVANCE:
        term = product.term_months
        parts.append(("factor charge", _money(amount * (Decimal(fees["factor_rate"]) - 1))))
    return sum((v for _, v in parts), ZERO), parts, term


# --------------------------------------------------------------------------- ranking


def normalise(weights: Mapping[str, Decimal] | None) -> dict[str, Decimal]:
    chosen = dict(DEFAULT_WEIGHTS)
    if weights:
        unknown = set(weights) - set(DEFAULT_WEIGHTS)
        if unknown:
            raise ValueError(f"unknown weights: {sorted(unknown)}")
        chosen.update({k: Decimal(v) for k, v in weights.items()})
    total = sum(chosen.values(), Decimal(0))
    if total <= 0 or any(v < 0 for v in chosen.values()):
        raise ValueError("weights must be non-negative and not all zero")
    return {k: (v / total).quantize(Decimal("0.0001")) for k, v in chosen.items()}


def match(snap: LedgerSnapshot, weights: Mapping[str, Decimal] | None = None) -> Match:
    w = normalise(weights)
    gap = find_gap(snap)
    if gap is None:
        return Match(None, [], w, "The projection shows no shortfall, so nothing needs funding.")
    options: list[Option] = []
    for product in sorted(snap.financing_products.values(), key=lambda p: p.id):
        limit, reasons = _limit(snap, product, gap)
        option = Option(
            product.id,
            product.provider,
            product.name,
            product.kind,
            eligible=not reasons,
            reasons=reasons,
            funded_in_days=product.time_to_funding_days,
        )
        if option.eligible:
            cost, parts, term = total_cost(snap, product, limit, gap.days)
            option.amount, option.days, option.total_cost = limit, gap.days, cost
            option.breakdown, option.full_term_months = parts, term
            option.in_time = product.time_to_funding_days <= gap.lead_days
        options.append(option)
    eligible = [o for o in options if o.eligible]
    if eligible:
        cheapest = min(o.total_cost / o.amount for o in eligible)
        for o in eligible:
            ratio = o.total_cost / o.amount
            o.scores = {
                "cost": HUNDRED if ratio == 0 else (HUNDRED * cheapest / ratio),
                "coverage": HUNDRED * o.amount / gap.amount,
                "speed": (
                    max(Decimal(50), HUNDRED - 2 * o.funded_in_days) if o.in_time else Decimal(0)
                ),
                "flexibility": FLEXIBILITY[o.kind],
                "collateral": Decimal(40)
                if snap.financing_products[o.product_id].requires_collateral
                else HUNDRED,
            }
            o.scores = {
                k: v.quantize(Decimal("0.1"), rounding=ROUND_HALF_UP) for k, v in o.scores.items()
            }
            o.score = sum((w[k] * v for k, v in o.scores.items()), Decimal(0)).quantize(
                Decimal("0.1"), rounding=ROUND_HALF_UP
            )
    options.sort(key=lambda o: (not o.eligible, -(o.score or 0), o.product_id))
    worst = min(snap.weeks, key=lambda w: (w.closing, w.index))
    nothing = (
        f"Projected cash stays below zero from {gap.need_by} to {gap.until}, with a low "
        f"of {format_eur(worst.closing)} in week {worst.index}. Nothing is paid for "
        "financing, and the shortfall is not covered."
    )
    return Match(gap, options, w, nothing)
