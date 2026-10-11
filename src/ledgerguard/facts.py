"""Facts: figures and statements computed by deterministic code, with stable ids.

Tools hand facts to the model, and the model cites them by id in its claims
instead of building derivations itself. The verifier never trusts a fact the
model echoes back. It calls the same functions on its own snapshot and
resolves each cited id there.

Some facts are mandatory. A fact is mandatory when a materiality condition
holds, such as a projected week closing below zero. If a response omits a
mandatory fact, the verifier puts it back and records ``MATERIAL_OMISSION``.
Checking that what was said is true is not enough. The verifier also checks
that what mattered was said.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import timedelta
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from ledgerguard.analytics import analysis, financing, radar
from ledgerguard.config import Policy
from ledgerguard.domain.enums import AccountType, ActionKind, Direction
from ledgerguard.money import format_eur
from ledgerguard.untrusted import neutralise
from ledgerguard.verifier.intent import Formula
from ledgerguard.verifier.snapshot import LedgerSnapshot

ZERO = Decimal("0.00")

# Tools whose results carry facts. The orchestrator records which ones a turn
# called, and the verifier uses that record to decide which facts are mandatory.
CASH_PROJECTION = "get_cash_projection"
REVENUE_CONCENTRATION = "get_revenue_concentration"
OPEN_INVOICES = "get_open_invoices"
FINANCING_OPTIONS = "get_financing_options"
FINANCIAL_SUMMARY = "get_financial_summary"
ANOMALIES = "get_anomalies"
# Run by the orchestrator on the model's drafted scenario, not chosen by the model.
SCENARIO = "run_scenario"
# The dry run: the rules evaluated on a draft action before the final answer.
# When it ran, the model has seen any block and can cite it as block.<RuleName>.
CHECK_ACTION = "check_action"
AUDIT_TRAIL = "get_audit_trail"
# Kinds that are about the cash position whatever tools ran, so the projection's
# mandatory facts always apply to them.
CASH_KINDS = frozenset(
    {
        ActionKind.DRAFT_FORECAST,
        ActionKind.RUN_SCENARIO,
        ActionKind.RANK_FINANCING,
    }
)


@dataclass(frozen=True)
class Fact:
    id: str
    statement: str
    figure: Decimal | None = None
    formula: Formula | None = None
    operands: tuple[str, ...] = ()
    params: dict[str, str] = field(default_factory=dict)
    source_row_ids: tuple[str, ...] = ()
    mandatory: bool = False
    materiality: str | None = None  # the condition that made it mandatory
    # Instruction-like spans removed from ledger text before it entered the statement.
    stripped: tuple[str, ...] = ()
    # What the model is shown instead of the statement, for facts whose values change
    # between turns. The owner always sees ``statement``, computed when the answer is
    # verified. The model sees a fixed sentence, so a recording still matches.
    model_statement: str | None = None
    # False for a fact the verifier computes only when it checks the answer, such as
    # the cost of the exact amount being accepted. The model never saw it, so leaving
    # it out is not an omission. It is still shown first.
    seen_by_model: bool = True

    def as_tool_dict(self) -> dict[str, Any]:
        """What the model sees. The derivation stays on the deterministic side."""
        shown = self.statement if self.model_statement is None else self.model_statement
        out: dict[str, Any] = {"fact_id": self.id, "statement": shown}
        if self.figure is not None:
            out["figure"] = str(self.figure)
        if self.mandatory:
            out["mandatory"] = True
        return out


def _cash_accounts(snap: LedgerSnapshot) -> tuple[str, ...]:
    return tuple(
        sorted(a.id for a in snap.accounts.values() if a.type is not AccountType.CREDIT_LINE)
    )


def _week_rows(snap: LedgerSnapshot, index: int, limit: int = 3) -> tuple[str, ...]:
    week = snap.weeks[index - 1]
    biggest = sorted(week.flows, key=lambda f: (-f.amount, f.source_id))[:limit]
    return tuple(dict.fromkeys(f.source_id for f in biggest))


def projection_facts(snap: LedgerSnapshot) -> list[Fact]:
    """Facts behind any answer about the 13-week cash position."""
    accounts = _cash_accounts(snap)
    facts = [
        Fact(
            id="cash.on_hand",
            statement=f"Cash on hand on {snap.as_of} is {format_eur(snap.cash_on_hand())}.",
            figure=snap.cash_on_hand(),
            formula=Formula.CASH_ON_HAND,
            operands=accounts,
            source_row_ids=accounts,
        )
    ]
    for w in snap.weeks:
        rows = (*accounts, *_week_rows(snap, w.index))
        facts.append(
            Fact(
                id=f"proj.w{w.index:02d}.closing",
                statement=f"Week {w.index}, ending {w.ends_on}, closes at {format_eur(w.closing)} "
                "on the full projection.",
                figure=w.closing,
                formula=Formula.PROJECTED_CLOSING,
                operands=rows,
                params={"week": str(w.index), "band": "all"},
                source_row_ids=rows,
            )
        )
        facts.append(
            Fact(
                id=f"proj.w{w.index:02d}.closing_contracted",
                statement=f"Week {w.index} closes at {format_eur(w.closing_contracted)} counting "
                "contracted lines only.",
                figure=w.closing_contracted,
                formula=Formula.PROJECTED_CLOSING,
                operands=rows,
                params={"week": str(w.index), "band": "contracted"},
                source_row_ids=rows,
            )
        )
    shortfall = shortfall_fact(snap)
    if shortfall is not None:
        facts.append(shortfall)
        facts += driver_facts(snap, int(shortfall.params["week"]))
    return facts


def shortfall_fact(snap: LedgerSnapshot) -> Fact | None:
    """Mandatory whenever any projected week closes below zero."""
    negative = [w for w in snap.weeks if w.closing < 0]
    if not negative:
        return None
    worst = min(negative, key=lambda w: (w.closing, w.index))
    weeks = [w.index for w in negative]
    span = _span(weeks)
    contracted = [w.index for w in snap.weeks if w.closing_contracted < 0]
    text = (
        f"Projected cash falls below zero in {span}. The projected low is week "
        f"{worst.index}, ending {worst.ends_on}, at {format_eur(worst.closing)}."
    )
    if contracted:
        text += (
            " Counting contracted lines only, projected cash is below zero from week "
            f"{contracted[0]}."
        )
    rows = (*_cash_accounts(snap), *_week_rows(snap, worst.index))
    return Fact(
        id="proj.shortfall",
        statement=text,
        figure=worst.closing,
        formula=Formula.PROJECTED_CLOSING,
        operands=rows,
        params={"week": str(worst.index), "band": "all"},
        source_row_ids=rows,
        mandatory=True,
        materiality="a projected week closes below zero",
    )


def driver_facts(snap: LedgerSnapshot, index: int, limit: int = 3) -> list[Fact]:
    """The largest outflows in one week, each a citeable fact."""
    week = snap.weeks[index - 1]
    outflows = sorted(
        (f for f in week.flows if f.direction is Direction.OUT),
        key=lambda f: (-f.amount, f.source_id),
    )[:limit]
    facts = []
    for rank, f in enumerate(outflows, start=1):
        formula, params = _row_formula(snap, f.source_table, f.source_id)
        label, stripped = neutralise(f.label)
        facts.append(
            Fact(
                id=f"proj.w{index:02d}.outflow_{rank}",
                statement=f"In week {index}, {label} takes {format_eur(f.amount)} on {f.on}.",
                figure=f.amount,
                formula=formula,
                operands=(f.source_id,),
                params=params,
                source_row_ids=(f.source_id,),
                stripped=tuple(stripped),
            )
        )
    return facts


def block_fact(rule: str, reason: str, rows: Iterable[str]) -> Fact:
    """A rule's BLOCK on the action under discussion. Always mandatory."""
    return Fact(
        id=f"block.{rule}",
        statement=f"This action is blocked by {rule}. {reason}",
        source_row_ids=tuple(rows),
        mandatory=True,
        materiality="a rule blocks the action under discussion",
    )


def _row_formula(snap: LedgerSnapshot, table: str, row_id: str) -> tuple[Formula, dict[str, str]]:
    if table == "invoice":
        return Formula.INVOICE_OUTSTANDING, {}
    if table == "payroll_run":
        return Formula.ROW_FIELD, {"field": "employer_cost_total"}
    return Formula.ROW_FIELD, {"field": "amount"}


def revenue_facts(snap: LedgerSnapshot, threshold: Decimal) -> list[Fact]:
    """Customer shares of trailing 12-month revenue. Mandatory above the threshold."""
    shares = snap.revenue_shares()
    facts = []
    for cp_id, share in sorted(shares.items(), key=lambda kv: (-kv[1], kv[0]))[:5]:
        percent = (share * 100).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        name, stripped = neutralise(snap.counterparties[cp_id].name)
        heavy = share > threshold
        facts.append(
            Fact(
                id=f"rev.share.{cp_id}",
                statement=f"{name} is {percent}% of net revenue over the last 12 months"
                + (
                    f", above the {threshold * 100:.0f}% concentration threshold." if heavy else "."
                ),
                figure=percent,
                formula=Formula.REVENUE_SHARE,
                operands=(cp_id,),
                source_row_ids=(cp_id,),
                stripped=tuple(stripped),
                mandatory=heavy,
                materiality="a customer is above the concentration threshold" if heavy else None,
            )
        )
    return facts


def open_invoice_facts(snap: LedgerSnapshot) -> list[Fact]:
    """Every invoice that still has money outstanding, issued or disputed.

    Counterparty names and descriptions come from outside the business, so they
    pass through ``neutralise`` before they enter a statement.
    """
    facts = []
    for inv in sorted(snap.invoices.values(), key=lambda i: (i.type.value, i.due_on, i.id)):
        owed = inv.gross_amount - inv.paid_amount
        if owed <= 0 or inv.status.value in ("draft", "paid"):
            continue
        name, s1 = neutralise(snap.counterparties[inv.counterparty_id].name)
        what, s2 = neutralise(inv.description)
        side = "Owed to us by" if inv.type.value == "receivable" else "We owe"
        facts.append(
            Fact(
                id=f"inv.{inv.id}",
                statement=f"{side} {name}: invoice {inv.id}, {inv.status.value}, due {inv.due_on}, "
                f"{format_eur(owed)} outstanding. Description: {what}",
                figure=owed,
                formula=Formula.INVOICE_OUTSTANDING,
                operands=(inv.id,),
                source_row_ids=(inv.id, inv.counterparty_id),
                stripped=tuple(s1 + s2),
            )
        )
    return facts


def _span(weeks: list[int]) -> str:
    if len(weeks) == 1:
        return f"week {weeks[0]}"
    if weeks == list(range(weeks[0], weeks[-1] + 1)):
        return f"weeks {weeks[0]} to {weeks[-1]}"
    return "weeks " + ", ".join(str(w) for w in weeks[:-1]) + f" and {weeks[-1]}"


# --------------------------------------------------------------------------- Phase 5 facts


def alert_facts(snap: LedgerSnapshot, policy: Policy) -> list[Fact]:
    """Every radar alert as a fact. High and critical alerts are mandatory.

    The shortfall alert is left out because ``proj.shortfall`` already states it.
    A high alert falling on or after the first negative week is citable but not
    mandatory: the shortfall already tells the owner that cash is gone by then,
    and a second inserted sentence about the same hole adds length, not warning.
    """
    proj = radar.baseline(snap)
    rules = policy.rules
    negative = [w for w in snap.weeks if w.closing < 0]
    covered_from = negative[0].starts_on if negative else None
    facts = []
    for alert in radar.compute_alerts(
        snap,
        proj,
        rules.protection_band,
        rules.payroll_margin_warning,
        rules.concentration_warning_share,
    ):
        if alert.kind == "SHORTFALL":
            continue
        falls_on = snap.as_of + timedelta(days=alert.lead_days)
        mandatory = alert.high and (covered_from is None or falls_on < covered_from)
        facts.append(
            Fact(
                id=f"alert.{alert.id}",
                statement=f"{alert.severity.capitalize()} alert, {alert.lead_days} days ahead: "
                f"{alert.headline}",
                source_row_ids=alert.rows,
                mandatory=mandatory,
                materiality="an alert at severity high or above" if mandatory else None,
            )
        )
    return facts


def financing_facts(snap: LedgerSnapshot) -> list[Fact]:
    """The gap, each option's total cost in euros, and what doing nothing means."""
    result = financing.match(snap)
    gap = result.gap
    business = (snap.business.id,)
    if gap is None:
        return [Fact(id="fin.no_gap", statement=result.do_nothing, source_row_ids=business)]
    facts = [
        Fact(
            id="fin.gap",
            statement=(
                f"The gap to fund is {format_eur(gap.amount)}, from {gap.need_by} to "
                f"{gap.until}, {gap.days} days. Funding has to arrive within "
                f"{gap.lead_days} days."
            ),
            figure=gap.amount,
            formula=Formula.METRIC,
            operands=business,
            params={"name": "financing_gap"},
            source_row_ids=business,
        )
    ]
    ranked = []
    for o in result.options:
        if not o.eligible:
            facts.append(
                Fact(
                    id=f"fin.{o.product_id}.ineligible",
                    statement=f"{o.provider}, {o.name}, is not available: {'; '.join(o.reasons)}.",
                    source_row_ids=(o.product_id,),
                )
            )
            continue
        ranked.append(o)
        term = f", counting the full {o.full_term_months}-month term" if o.full_term_months else ""
        cover = (o.amount * 100 / gap.amount).quantize(Decimal("1"))
        facts.append(
            Fact(
                id=f"fin.{o.product_id}.total_cost",
                statement=(
                    f"{o.provider}, {o.name}: borrowing {format_eur(o.amount)} for "
                    f"{o.days} days costs {format_eur(o.total_cost)} in total{term}. It "
                    f"covers {cover}% of the gap and can be funded in {o.funded_in_days} "
                    "days."
                ),
                figure=o.total_cost,
                formula=Formula.FINANCING_TOTAL_COST,
                operands=(o.product_id,),
                params={"amount": str(o.amount), "days": str(o.days)},
                source_row_ids=(o.product_id,),
            )
        )
    # Every financing answer carries the ranking with total costs in euros and the
    # do-nothing baseline, so both are mandatory once this tool has run.
    if ranked:
        order = "; ".join(
            f"{n}. {o.provider}, {o.name}, {format_eur(o.total_cost)} in total for "
            f"{format_eur(o.amount)} of the {format_eur(gap.amount)} gap"
            for n, o in enumerate(ranked[:3], 1)
        )
        facts.append(
            Fact(
                id="fin.ranking",
                statement=f"Ranked on cost, coverage, speed, flexibility and collateral: {order}.",
                source_row_ids=(*business, *(o.product_id for o in ranked[:3])),
                mandatory=True,
                materiality="a financing answer always states total cost in euros",
            )
        )
    facts.append(
        Fact(
            id="fin.do_nothing",
            statement=f"Doing nothing: {result.do_nothing}",
            source_row_ids=business,
            mandatory=True,
            materiality="a financing answer always shows the do-nothing baseline",
        )
    )
    return facts


METRIC_LABELS = {
    "revenue_12m": "Revenue over the last 12 months",
    "cost_of_goods_12m": "Cost of goods over the last 12 months",
    "operating_costs_12m": "Operating costs over the last 12 months",
    "gross_margin": "Gross margin",
    "net_margin": "Net margin",
    "monthly_operating_cash_flow": "Average monthly operating cash flow",
    "burn_rate": "Monthly burn",
    "runway_days": "Runway",
    "dso": "Days sales outstanding",
    "dpo": "Days payables outstanding",
    "cash_conversion_cycle": "Cash conversion cycle",
}


def _render(value: Decimal, unit: str) -> str:
    if unit == "money":
        return format_eur(value)
    if unit == "percent":
        return f"{value}%"
    return f"{value} days"


def summary_facts(snap: LedgerSnapshot) -> list[Fact]:
    business = (snap.business.id,)
    return [
        Fact(
            id=f"ana.{m.name}",
            statement=f"{METRIC_LABELS[m.name]} is {_render(m.value, m.unit)}. {m.method}",
            figure=m.value,
            formula=Formula.METRIC,
            operands=business,
            params={"name": m.name},
            source_row_ids=business,
        )
        for m in analysis.metrics(snap).values()
    ]


def anomaly_facts(snap: LedgerSnapshot) -> list[Fact]:
    facts = []
    for a in analysis.anomalies(snap):
        row = a.rows[-1]
        field = "gross_amount" if row.startswith("inv_") else "amount"
        facts.append(
            Fact(
                id=a.id,
                statement=a.statement,
                figure=a.amount,
                formula=Formula.ROW_FIELD,
                operands=(row,),
                params={"field": field},
                source_row_ids=a.rows,
                stripped=a.stripped,
            )
        )
    return facts


AUDIT_LABELS = {
    "intent.verified": "verified turns",
    "intent.rejected": "rejected turns",
    "intent.blocked": "blocked actions",
    "action.executed": "executed actions",
    "action.refused": "refused executions",
    "approval.declined": "declined approvals",
    "untrusted.neutralised": "records of instruction-like text removed",
    "intent.failed": "failed actions",
    "system.started": "starts",
}


def audit_facts(snap: LedgerSnapshot) -> list[Fact]:
    """The audit chain's state, computed fresh when the answer is verified.

    The chain grows with every turn, so the model is shown a fixed sentence and
    the owner sees the verifier's own, current one. Event payloads are never
    shown to the model: they include the instruction-like text that was removed
    from ledger data, and echoing it would inject it again.
    """
    a = snap.audit
    if a is None:
        return []
    status = "intact" if a.intact else f"broken at event {a.first_bad_seq}: {a.reason}"
    counts = ", ".join(
        f"{n} {AUDIT_LABELS.get(kind, kind)}" for kind, n in sorted(a.counts_on_day.items())
    )
    statement = (
        f"The audit chain is {status}. It holds {a.events} events, each hashed with the one "
        f"before it, and its head hash is {a.head_hash}."
    )
    if a.day is not None:
        statement += f" On {a.day}, the day of the latest event, it recorded {counts}."
    return [
        Fact(
            id="audit.chain",
            statement=statement,
            source_row_ids=(snap.business.id,),
            mandatory=True,
            materiality="an answer about the audit trail always says whether the chain verifies",
            model_statement=(
                "The verifier states whether the audit chain is intact, its length, its head "
                "hash and the day's events when your answer is shown. Cite this fact as "
                "audit.chain and do not state those values yourself."
            ),
        )
    ]


def scenario_facts(snap: LedgerSnapshot, policy: Policy, scenario: dict[str, Any]) -> list[Fact]:
    """What a what-if scenario changes. The headline result is mandatory."""
    rules = policy.rules
    result = radar.simulate(
        snap,
        scenario,
        rules.protection_band,
        rules.payroll_margin_warning,
        rules.concentration_warning_share,
    )
    text = radar.canonical_scenario(scenario)
    rows = tuple(r for r in result.rows if r.split("_", 1)[0] in ("inv", "cp", "acc"))
    rows = rows or _cash_accounts(snap)
    low, before = result.lowest("after"), result.lowest("before")
    statement = (
        f"With {result.description}, projected cash reaches its low in week "
        f"{low.index}, ending {low.ends_on}, at {format_eur(low.closing)}, against "
        f"{format_eur(before.closing)} in week {before.index} without the change."
    )
    # Names echoed back from the ledger are neutralised in the description. Their
    # removed spans are recorded here, so the echo path is logged like any other.
    involved = {r for r in result.rows if r.startswith("cp_")} | {
        snap.invoices[r].counterparty_id for r in result.rows if r in snap.invoices
    }
    stripped = tuple(
        span for cp in sorted(involved) for span in neutralise(snap.counterparties[cp].name)[1]
    )
    new = [a.headline for a in result.new_alerts if a.high]
    if new:
        statement += " New high alerts: " + " ".join(new)
    cleared = [a.id for a in result.cleared_alerts]
    if cleared:
        statement += f" It clears {len(cleared)} alert{'s' if len(cleared) > 1 else ''}."
    facts = [
        Fact(
            id="scn.result",
            statement=statement,
            figure=low.closing,
            formula=Formula.SCENARIO_CLOSING,
            operands=rows,
            params={"scenario": text, "week": str(low.index), "band": "all"},
            source_row_ids=rows,
            mandatory=True,
            materiality="the result of the scenario asked for",
            stripped=stripped,
        )
    ]
    for w in result.after.weeks:
        facts.append(
            Fact(
                id=f"scn.w{w.index:02d}.closing",
                statement=f"With the change, week {w.index} closes at {format_eur(w.closing)}.",
                figure=w.closing,
                formula=Formula.SCENARIO_CLOSING,
                operands=rows,
                params={"scenario": text, "week": str(w.index), "band": "all"},
                source_row_ids=rows,
            )
        )
    return facts


def tool_facts(
    snap: LedgerSnapshot, tool: str, policy: Policy, payload: dict[str, Any] | None = None
) -> list[Fact]:
    """The facts one tool produces. Tools and the verifier both call this."""
    if tool == CASH_PROJECTION:
        return projection_facts(snap) + alert_facts(snap, policy)
    if tool == REVENUE_CONCENTRATION:
        return revenue_facts(snap, policy.rules.concentration_warning_share)
    if tool == OPEN_INVOICES:
        return open_invoice_facts(snap)
    if tool == FINANCING_OPTIONS:
        return financing_facts(snap)
    if tool == FINANCIAL_SUMMARY:
        return summary_facts(snap)
    if tool == ANOMALIES:
        return anomaly_facts(snap)
    if tool == AUDIT_TRAIL:
        return audit_facts(snap)
    if tool == SCENARIO and payload and "scenario" in payload:
        try:
            return scenario_facts(snap, policy, payload["scenario"])
        except ValueError:
            # The payload names rows that do not exist. Payload resolution rejects
            # the turn with the reason, so there is nothing for a claim to cite.
            return []
    return []


def facts_for_turn(
    snap: LedgerSnapshot,
    kind: ActionKind | None,
    tools: Iterable[str],
    policy: Policy,
    payload: dict[str, Any] | None = None,
) -> dict[str, Fact]:
    """Every fact a turn can cite, by id."""
    called = list(dict.fromkeys(tools))
    if kind in CASH_KINDS and CASH_PROJECTION not in called:
        called.append(CASH_PROJECTION)
    facts: list[Fact] = []
    for tool in called:
        facts += tool_facts(snap, tool, policy, payload)
    if kind is ActionKind.ACCEPT_FINANCING_OFFER and payload:
        facts += accept_cost_facts(snap, payload)
    return {f.id: f for f in facts}


def accept_cost_facts(snap: LedgerSnapshot, payload: dict[str, Any]) -> list[Fact]:
    """The total cost of exactly what is being accepted, stated by the verifier.

    The ranking costs each product for the amount it can cover. The model may
    accept a different amount, for example after the outbound ceiling blocks
    the first one. The person typing the T3 confirmation must see the cost of
    the amount they are confirming, in the verifier's words.
    """
    product = snap.financing_products.get(str(payload.get("product_id")))
    try:
        amount = Decimal(str(payload.get("amount"))).quantize(Decimal("0.01"))
    except ArithmeticError:
        return []
    if product is None or amount <= 0:
        return []
    gap = financing.find_gap(snap)
    days = gap.days if gap is not None else 30
    cost, _, term = financing.total_cost(snap, product, amount, days)
    over = f"over its full {term}-month term" if term else f"over the {days} days of the gap"
    return [
        Fact(
            id="fin.accept.total_cost",
            statement=(
                f"Accepting {product.provider}, {product.name}, for {format_eur(amount)} "
                f"costs {format_eur(cost)} in total, {over}. This is the cost being confirmed."
            ),
            figure=cost,
            formula=Formula.FINANCING_TOTAL_COST,
            operands=(product.id,),
            params={"amount": str(amount), "days": str(days)},
            source_row_ids=(product.id,),
            mandatory=True,
            materiality="accepting financing always states the total cost of the amount accepted",
            seen_by_model=False,
        )
    ]
