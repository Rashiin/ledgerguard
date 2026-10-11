"""Stage 4b: the verifier checks that what mattered was said, not only that it was true."""

from __future__ import annotations

import dataclasses
import json
from decimal import Decimal
from typing import Any

import pytest

from ledgerguard.config import Policy
from ledgerguard.facts import CASH_PROJECTION, REVENUE_CONCENTRATION, facts_for_turn
from ledgerguard.verifier.pipeline import TurnContext, Verification, Verifier
from ledgerguard.verifier.seal import Sealer
from ledgerguard.verifier.snapshot import LedgerSnapshot

from .conftest import intent_json

CASH = TurnContext(tools_called=(CASH_PROJECTION,))


def verify(
    snapshot: LedgerSnapshot,
    policy: Policy,
    kind: str,
    payload: dict[str, Any],
    claims: list[dict[str, Any]],
    context: TurnContext | None = None,
    rationale: str = "Stub.",
) -> Verification:
    raw = intent_json(kind, payload, claims, rationale)
    return Verifier(Sealer(bytes(32)), policy).verify(raw, snapshot, context)


def scenario_1_without_the_shortfall(snapshot: LedgerSnapshot, policy: Policy) -> Verification:
    """The first live probe in miniature: true statements, the shortfall left out."""
    return verify(
        snapshot,
        policy,
        "ANSWER_QUERY",
        {"topic": "next quarter"},
        [
            {"statement": "Cash on hand today is €129,120.63.", "fact_id": "cash.on_hand"},
            {"statement": "The quarter ends at €97,320.87.", "fact_id": "proj.w13.closing"},
        ],
        CASH,
    )


def test_omitted_shortfall_is_put_back_first(snapshot: LedgerSnapshot, policy: Policy) -> None:
    v = scenario_1_without_the_shortfall(snapshot, policy)
    assert "MATERIAL_OMISSION" in v.receipt.risk_flags
    first = v.presented["claims"][0]
    assert first["status"] == "inserted_by_verifier"
    assert first["fact_id"] == "proj.shortfall"
    assert first["figure"] == "-219676.22"
    assert "weeks 6 to 8" in first["statement"] and "week 7" in first["statement"]
    assert "contracted lines only" in first["statement"]
    assert v.receipt.material_omissions == [
        {
            "fact_id": "proj.shortfall",
            "inserted": first["statement"],
            "why": "a projected week closes below zero",
        }
    ]
    # The model's own phrasing survives for everything else.
    assert [c["statement"] for c in v.presented["claims"][1:]] == [
        "Cash on hand today is €129,120.63.",
        "The quarter ends at €97,320.87.",
    ]
    assert v.outcome == "verified"


def test_citing_the_mandatory_fact_satisfies_it(snapshot: LedgerSnapshot, policy: Policy) -> None:
    v = verify(
        snapshot,
        policy,
        "ANSWER_QUERY",
        {"topic": "next quarter"},
        [
            {
                "statement": "We are short in weeks 6 to 8, worst in week 7 at -€219,676.22.",
                "fact_id": "proj.shortfall",
            },
        ],
        CASH,
    )
    assert "MATERIAL_OMISSION" not in v.receipt.risk_flags
    assert v.receipt.material_omissions == []
    assert v.presented["claims"][0]["status"] == "verified"


def test_mentioning_it_in_prose_without_citing_does_not_count(
    snapshot: LedgerSnapshot, policy: Policy
) -> None:
    v = verify(
        snapshot,
        policy,
        "ANSWER_QUERY",
        {"topic": "q"},
        [{"statement": "Cash on hand today.", "fact_id": "cash.on_hand"}],
        CASH,
        rationale="Note that weeks 6 to 8 are negative.",
    )
    assert "MATERIAL_OMISSION" in v.receipt.risk_flags


def test_a_cited_mandatory_fact_that_gets_removed_is_still_an_omission(
    snapshot: LedgerSnapshot, policy: Policy
) -> None:
    v = verify(
        snapshot,
        policy,
        "ANSWER_QUERY",
        {"topic": "q"},
        [
            {
                "statement": "Short in week 7.",
                "fact_id": "proj.shortfall",
                "source_row_ids": ["inv_9999"],
            }
        ],
        CASH,
    )
    assert "MATERIAL_OMISSION" in v.receipt.risk_flags


def test_forecasts_must_carry_the_shortfall_whatever_tools_ran(
    snapshot: LedgerSnapshot, policy: Policy
) -> None:
    v = verify(snapshot, policy, "DRAFT_FORECAST", {"weeks": 13}, [])
    assert v.receipt.material_omissions[0]["fact_id"] == "proj.shortfall"


def test_unrelated_answers_have_no_cash_obligation(
    snapshot: LedgerSnapshot, policy: Policy
) -> None:
    v = verify(snapshot, policy, "ANSWER_QUERY", {"topic": "who is our largest supplier"}, [])
    assert "MATERIAL_OMISSION" not in v.receipt.risk_flags


def test_no_shortfall_means_nothing_is_mandatory(snapshot: LedgerSnapshot, policy: Policy) -> None:
    lift = Decimal("500000.00")
    snapshot.weeks = [
        dataclasses.replace(
            w, closing=w.closing + lift, closing_contracted=w.closing_contracted + lift
        )
        for w in snapshot.weeks
    ]
    v = verify(snapshot, policy, "ANSWER_QUERY", {"topic": "q"}, [], CASH)
    assert "MATERIAL_OMISSION" not in v.receipt.risk_flags
    assert "proj.shortfall" not in facts_for_turn(
        snapshot, None, [CASH_PROJECTION], Decimal("0.30")
    )


def test_revenue_answers_must_name_the_concentrated_customer(
    snapshot: LedgerSnapshot, policy: Policy
) -> None:
    v = verify(
        snapshot,
        policy,
        "ANSWER_QUERY",
        {"topic": "revenue mix"},
        [],
        TurnContext(tools_called=(REVENUE_CONCENTRATION,)),
    )
    assert [o["fact_id"] for o in v.receipt.material_omissions] == ["rev.share.cp_brennan"]
    assert "Brennan Hospitality Group" in v.presented["claims"][0]["statement"]


def test_a_block_is_always_stated_first(snapshot: LedgerSnapshot, policy: Policy) -> None:
    v = verify(snapshot, policy, "RECORD_PAYMENT", {"invoice_id": "inv_0620"}, [])
    assert v.outcome == "blocked"
    first = v.presented["claims"][0]
    assert first["fact_id"] == "block.DisputedInvoiceLock"
    assert first["statement"].startswith("This action is blocked by DisputedInvoiceLock.")


# ---- fact citations


def test_cited_fact_brings_its_own_derivation(snapshot: LedgerSnapshot, policy: Policy) -> None:
    v = verify(
        snapshot,
        policy,
        "ANSWER_QUERY",
        {"topic": "q"},
        [{"statement": "Week 7 closes deep in the red.", "fact_id": "proj.w07.closing"}],
        CASH,
    )
    rc = v.receipt.recomputations[0]
    assert rc["status"] == "match" and rc["recomputed"] == "-219676.22"
    assert rc["formula"] == "opening + Σin − Σout for the chosen band"


def test_wrong_figure_on_a_cited_fact_is_corrected(
    snapshot: LedgerSnapshot, policy: Policy
) -> None:
    v = verify(
        snapshot,
        policy,
        "ANSWER_QUERY",
        {"topic": "q"},
        [
            {
                "statement": "Cash on hand is €179,774.36.",
                "fact_id": "cash.on_hand",
                "figure": "179774.36",
            }
        ],
        CASH,
    )
    assert "FIGURE_MISMATCH" in v.receipt.risk_flags
    assert "€129,120.63" in v.presented["claims"][1]["statement"]


def test_an_invented_fact_id_is_removed_visibly(snapshot: LedgerSnapshot, policy: Policy) -> None:
    v = verify(
        snapshot,
        policy,
        "ANSWER_QUERY",
        {"topic": "q"},
        [{"statement": "Week 5 is fine.", "fact_id": "week_5"}],
        CASH,
    )
    removed = next(c for c in v.presented["claims"] if c["status"] == "removed")
    assert "week_5, which no tool in this turn produced" in removed["statement"]


def test_a_fact_from_a_tool_that_did_not_run_cannot_be_cited(
    snapshot: LedgerSnapshot, policy: Policy
) -> None:
    v = verify(
        snapshot,
        policy,
        "ANSWER_QUERY",
        {"topic": "q"},
        [{"statement": "Brennan is big.", "fact_id": "rev.share.cp_brennan"}],
        CASH,
    )
    assert "UNSUPPORTED_CLAIM_REMOVED" in v.receipt.risk_flags


def test_facts_are_deterministic(snapshot: LedgerSnapshot) -> None:
    first = facts_for_turn(
        snapshot, None, [CASH_PROJECTION, REVENUE_CONCENTRATION], Decimal("0.30")
    )
    second = facts_for_turn(
        snapshot, None, [CASH_PROJECTION, REVENUE_CONCENTRATION], Decimal("0.30")
    )
    assert first == second and len(first) > 20


# ---- currency


@pytest.mark.parametrize(
    "statement, shown",
    [
        ("Cash on hand is $129,120.63.", "Cash on hand is €129,120.63."),
        ("Cash on hand is 129,120.63 dollars.", "Cash on hand is €129,120.63."),
        ("Cash on hand is $1,000.", "Cash on hand is [figure removed: not verified]."),
    ],
)
def test_wrong_currency_is_its_own_finding(
    snapshot: LedgerSnapshot, policy: Policy, statement: str, shown: str
) -> None:
    v = verify(
        snapshot,
        policy,
        "ANSWER_QUERY",
        {"topic": "q"},
        [{"statement": statement, "fact_id": "cash.on_hand"}],
        CASH,
    )
    assert "CURRENCY_MISMATCH" in v.receipt.risk_flags
    assert shown in [c["statement"] for c in v.presented["claims"]]
    assert any(f["reason"].startswith("currency") for f in v.receipt.text_corrections)


def test_currency_named_without_a_number_is_still_flagged(
    snapshot: LedgerSnapshot, policy: Policy
) -> None:
    v = verify(
        snapshot,
        policy,
        "ANSWER_QUERY",
        {"topic": "q"},
        [],
        None,
        rationale="All figures are in USD.",
    )
    assert "CURRENCY_MISMATCH" in v.receipt.risk_flags


def test_euros_raise_nothing(snapshot: LedgerSnapshot, policy: Policy) -> None:
    v = verify(
        snapshot,
        policy,
        "ANSWER_QUERY",
        {"topic": "q"},
        [{"statement": "Cash on hand is €129,120.63.", "fact_id": "cash.on_hand"}],
        CASH,
    )
    assert "CURRENCY_MISMATCH" not in v.receipt.risk_flags


def test_receipt_records_the_tools_that_ran(snapshot: LedgerSnapshot, policy: Policy) -> None:
    v = scenario_1_without_the_shortfall(snapshot, policy)
    assert v.receipt.tools_called == [CASH_PROJECTION]
    assert json.loads(v.receipt.model_dump_json())["material_omissions"]
