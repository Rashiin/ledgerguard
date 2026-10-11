"""Read-only tools the model can see. Each returns ready-made facts.

A tool result is a list of facts, each with an id, a statement written by
code and a figure. The model cites facts by id instead of building
derivations, which removes the construction errors seen in the first probe:
an invented row id, a missing field name, a wrong formula. Facts marked
``mandatory`` must appear in the answer, and the verifier checks that.

Tools never write. They run on the read-only snapshot.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from ledgerguard import facts
from ledgerguard.verifier.snapshot import LedgerSnapshot


@dataclass(frozen=True)
class ToolResult:
    name: str
    facts: list[facts.Fact]

    def for_model(self) -> dict[str, Any]:
        return {"tool": self.name, "facts": [f.as_tool_dict() for f in self.facts]}


@dataclass(frozen=True)
class Tool:
    name: str
    description: str
    run: Callable[[LedgerSnapshot, Decimal], ToolResult]


def _cash_projection(snap: LedgerSnapshot, threshold: Decimal) -> ToolResult:
    return ToolResult(facts.CASH_PROJECTION, facts.projection_facts(snap))


def _revenue_concentration(snap: LedgerSnapshot, threshold: Decimal) -> ToolResult:
    return ToolResult(facts.REVENUE_CONCENTRATION, facts.revenue_facts(snap, threshold))


def _open_invoices(snap: LedgerSnapshot, threshold: Decimal) -> ToolResult:
    return ToolResult(facts.OPEN_INVOICES, facts.open_invoice_facts(snap))


TOOLS: dict[str, Tool] = {
    facts.CASH_PROJECTION: Tool(
        facts.CASH_PROJECTION,
        "Cash on hand and the 13-week projection: each week's closing balance on the full "
        "model and on contracted lines only, any shortfall, and what drives the worst week.",
        _cash_projection,
    ),
    facts.OPEN_INVOICES: Tool(
        facts.OPEN_INVOICES,
        "Every invoice with money outstanding, both directions, with its status and due date.",
        _open_invoices,
    ),
    facts.REVENUE_CONCENTRATION: Tool(
        facts.REVENUE_CONCENTRATION,
        "The largest customers' shares of net revenue over the last 12 months.",
        _revenue_concentration,
    ),
}


def run_tools(snap: LedgerSnapshot, names: list[str], threshold: Decimal) -> list[ToolResult]:
    return [TOOLS[name].run(snap, threshold) for name in names]
