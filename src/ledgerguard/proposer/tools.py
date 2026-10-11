"""Read-only tools the model can see. Each returns ready-made facts.

A tool result is a list of facts, each with an id, a statement written by
code and a figure. The model cites facts by id instead of building
derivations, which removes the construction errors seen in the first probe:
an invented row id, a missing field name, a wrong formula. Facts marked
``mandatory`` must appear in the answer, and the verifier checks that.

Tools never write. They run on the read-only snapshot.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ledgerguard import facts
from ledgerguard.config import Policy
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


TOOLS: dict[str, Tool] = {
    facts.CASH_PROJECTION: Tool(
        facts.CASH_PROJECTION,
        "Cash on hand, the 13-week projection on the full model and on contracted lines only, "
        "any shortfall and what drives the worst week, and every radar alert.",
    ),
    facts.OPEN_INVOICES: Tool(
        facts.OPEN_INVOICES,
        "Every invoice with money outstanding, both directions, with its status and due date.",
    ),
    facts.REVENUE_CONCENTRATION: Tool(
        facts.REVENUE_CONCENTRATION,
        "The largest customers' shares of net revenue over the last 12 months.",
    ),
    facts.FINANCING_OPTIONS: Tool(
        facts.FINANCING_OPTIONS,
        "The gap to fund, each synthetic financing option's total cost in euros, the options "
        "ruled out and what doing nothing means.",
    ),
    facts.FINANCIAL_SUMMARY: Tool(
        facts.FINANCIAL_SUMMARY,
        "Margins, cash flow, burn, runway, DSO, DPO and the cash conversion cycle, each with "
        "its method.",
    ),
    facts.ANOMALIES: Tool(
        facts.ANOMALIES,
        "Transactions far from their usual amount, supplier invoices that look duplicated, and "
        "weekend manual bookings.",
    ),
}


def run_tools(snap: LedgerSnapshot, names: list[str], policy: Policy) -> list[ToolResult]:
    return [ToolResult(name, facts.tool_facts(snap, name, policy)) for name in names]


def scenario_result(snap: LedgerSnapshot, policy: Policy, scenario: dict[str, Any]) -> ToolResult:
    """Run by the orchestrator on the scenario the model drafted."""
    return ToolResult(facts.SCENARIO, facts.scenario_facts(snap, policy, scenario))
