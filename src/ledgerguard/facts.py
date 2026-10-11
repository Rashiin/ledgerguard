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
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

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
# The dry run: the rules evaluated on a draft action before the final answer.
# When it ran, the model has seen any block and can cite it as block.<RuleName>.
CHECK_ACTION = "check_action"
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

    def as_tool_dict(self) -> dict[str, Any]:
        """What the model sees. The derivation stays on the deterministic side."""
        out: dict[str, Any] = {"fact_id": self.id, "statement": self.statement}
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


def facts_for_turn(
    snap: LedgerSnapshot,
    kind: ActionKind | None,
    tools: Iterable[str],
    concentration_threshold: Decimal,
) -> dict[str, Fact]:
    """Every fact a turn can cite, by id. Tools and the verifier both call this."""
    facts: list[Fact] = []
    called = set(tools)
    if kind in CASH_KINDS:
        called.add(CASH_PROJECTION)
    if CASH_PROJECTION in called:
        facts += projection_facts(snap)
    if REVENUE_CONCENTRATION in called:
        facts += revenue_facts(snap, concentration_threshold)
    if OPEN_INVOICES in called:
        facts += open_invoice_facts(snap)
    return {f.id: f for f in facts}
