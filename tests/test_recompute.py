"""Stage 2, recomputation, and stage 4, provenance."""

from __future__ import annotations

from decimal import Decimal
from typing import Any

import pytest

from ledgerguard.verifier.intent import parse_intent
from ledgerguard.verifier.provenance import check_claim
from ledgerguard.verifier.recompute import recompute_claim, resolve_payload
from ledgerguard.verifier.snapshot import LedgerSnapshot

from .conftest import intent_json


def claim(
    figure: str | None,
    formula: str | None,
    operands: list[str],
    params: dict[str, str] | None = None,
    sources: list[str] | None = None,
) -> Any:
    raw: dict[str, Any] = {
        "statement": "s",
        "source_row_ids": sources if sources is not None else operands,
    }
    if figure is not None:
        raw["figure"] = figure
    if formula is not None:
        raw["derivation"] = {"formula": formula, "operands": operands, "params": params or {}}
    intent = parse_intent(intent_json("ANSWER_QUERY", {"topic": "t"}, [raw])).intent
    assert intent is not None
    return intent.claims[0]


def run(snapshot: LedgerSnapshot, c: Any) -> Any:
    return recompute_claim(snapshot, 0, c, set())


def test_matching_figure(snapshot: LedgerSnapshot) -> None:
    rc = run(snapshot, claim("46854.00", "INVOICE_OUTSTANDING", ["inv_0412"]))
    assert rc.status == "match" and rc.recomputed == Decimal("46854.00")


def test_wrong_figure_is_replaced_by_the_recomputed_value(snapshot: LedgerSnapshot) -> None:
    rc = run(
        snapshot, claim("18400.00", "PROJECTED_CLOSING", ["inv_0665"], {"week": "7", "band": "all"})
    )
    assert rc.status == "mismatch"
    assert rc.model_figure == Decimal("18400.00")
    assert rc.recomputed == snapshot.weeks[6].closing
    assert rc.as_dict()["match"] is False


@pytest.mark.parametrize(
    "formula, operands, params, expected",
    [
        ("ROW_FIELD", ["pay_0026"], {"field": "employer_cost_total"}, "73576.18"),
        ("ACCOUNT_BALANCE", ["acc_credit_line"], {}, "0.00"),
        ("CASH_ON_HAND", ["acc_operating", "acc_petty_cash"], {}, "129120.63"),
        ("SUM_TRANSACTIONS", ["txn_0869"], {}, "1121.64"),
        ("REVENUE_SHARE", ["cp_brennan"], {}, None),
        ("PROJECTED_CLOSING", ["acc_operating"], {"week": "7", "band": "contracted"}, None),
        ("PROJECTED_INFLOW", ["inv_0678"], {"week": "9", "confidence": "CONTRACTED"}, None),
        ("PROJECTED_OUTFLOW", ["inv_0665"], {"week": "7", "confidence": "all"}, None),
    ],
)
def test_each_formula_recomputes(
    snapshot: LedgerSnapshot,
    formula: str,
    operands: list[str],
    params: dict[str, str],
    expected: str | None,
) -> None:
    rc = run(snapshot, claim("1.00", formula, operands, params))
    assert rc.status in ("match", "mismatch"), rc.reason
    if expected is not None:
        assert rc.recomputed == Decimal(expected)


def test_projection_formulas_agree_with_the_projection(snapshot: LedgerSnapshot) -> None:
    w7 = snapshot.weeks[6]
    contracted = run(
        snapshot,
        claim("0", "PROJECTED_CLOSING", ["acc_operating"], {"week": "7", "band": "contracted"}),
    )
    outflow = run(snapshot, claim("0", "PROJECTED_OUTFLOW", ["inv_0665"], {"week": "7"}))
    assert contracted.recomputed == w7.closing_contracted
    assert outflow.recomputed == w7.total_outflow


@pytest.mark.parametrize(
    "figure, formula, operands, params, reason",
    [
        ("100.00", None, ["inv_0412"], {}, "no derivation"),
        ("100.00", "INVOICE_OUTSTANDING", ["inv_9999"], {}, "does not exist"),
        ("100.00", "INVOICE_OUTSTANDING", ["txn_0869"], {}, "is a transaction row"),
        ("100.00", "ROW_FIELD", ["inv_0412", "inv_0377"], {"field": "gross_amount"}, "exactly one"),
        ("100.00", "ROW_FIELD", ["inv_0412"], {"field": "description"}, "no monetary field"),
        ("100.00", "PROJECTED_CLOSING", ["inv_0665"], {"week": "14"}, "1-13"),
        ("100.00", "PROJECTED_CLOSING", ["inv_0665"], {"week": "7", "band": "hopeful"}, "band"),
        ("100.00", "CASH_ON_HAND", ["acc_credit_line"], {}, "headroom"),
    ],
)
def test_unrecomputable_figures_are_marked(
    snapshot: LedgerSnapshot,
    figure: str,
    formula: str | None,
    operands: list[str],
    params: dict[str, str],
    reason: str,
) -> None:
    rc = run(snapshot, claim(figure, formula, operands, params))
    assert rc.status == "not_recomputable"
    assert reason in (rc.reason or "")
    assert rc.recomputed is None


# --------------------------------------------------------------------------- provenance


def provenance(snapshot: LedgerSnapshot, c: Any) -> Any:
    return check_claim(snapshot, 0, c, run(snapshot, c), set())


def test_uncited_claim_is_removed(snapshot: LedgerSnapshot) -> None:
    finding = provenance(snapshot, claim(None, None, [], sources=[]))
    assert finding.status == "removed" and "cites no source rows" in finding.reason


def test_claim_citing_missing_rows_is_removed(snapshot: LedgerSnapshot) -> None:
    finding = provenance(snapshot, claim(None, None, [], sources=["inv_0412", "inv_9999"]))
    assert finding.status == "removed" and "inv_9999" in finding.reason


def test_claim_citing_the_wrong_kind_of_row_is_removed(snapshot: LedgerSnapshot) -> None:
    c = claim("1121.64", "SUM_TRANSACTIONS", ["txn_0869"], sources=["txn_0869", "pay_0026"])
    finding = provenance(snapshot, c)
    assert finding.status == "removed" and "pay_0026" in finding.reason


def test_unverifiable_figure_keeps_the_statement_but_loses_the_figure(
    snapshot: LedgerSnapshot,
) -> None:
    finding = provenance(snapshot, claim("99.00", None, ["inv_0412"]))
    assert finding.status == "figure_removed"


def test_supported_claim(snapshot: LedgerSnapshot) -> None:
    c = claim("46854.00", "INVOICE_OUTSTANDING", ["inv_0412"], sources=["inv_0412", "cp_brennan"])
    assert provenance(snapshot, c).status == "supported"


# --------------------------------------------------------------------------- payload resolution


def resolve(snapshot: LedgerSnapshot, kind: str, payload: dict[str, Any]) -> Any:
    intent = parse_intent(intent_json(kind, payload)).intent
    assert intent is not None
    return resolve_payload(snapshot, intent, set())


def test_payment_amounts_come_from_the_ledger(snapshot: LedgerSnapshot) -> None:
    res = resolve(snapshot, "INITIATE_PAYMENT", {"invoice_ids": ["inv_0377", "inv_0665"]})
    assert res.errors == []
    assert [str(p.amount) for p in res.payments] == ["4312.50", "142500.00"]
    assert res.amount == Decimal("146812.50")
    assert res.replaces == frozenset({"inv_0377", "inv_0665"})


@pytest.mark.parametrize(
    "kind, payload, error",
    [
        ("INITIATE_PAYMENT", {"invoice_ids": ["inv_0412"]}, "not a payable"),
        ("INITIATE_PAYMENT", {"invoice_ids": ["inv_0641"]}, "nothing to pay"),
        ("INITIATE_PAYMENT", {"invoice_ids": ["inv_0377", "inv_0377"]}, "listed twice"),
        ("INITIATE_PAYMENT", {"invoice_ids": ["inv_0377"], "pay_on": "2026-01-01"}, "before"),
        ("RECORD_PAYMENT", {"invoice_id": "inv_0412", "amount": "50000.00"}, "between"),
        ("RECORD_PAYMENT", {"invoice_id": "inv_0377"}, "not a receivable"),
        ("MARK_DISPUTED", {"invoice_id": "inv_9999", "reason": "x"}, "does not exist"),
        ("ACCEPT_FINANCING_OFFER", {"product_id": "fin_if_01", "amount": "1.00"}, "lends"),
        (
            "DRAFT_INVOICE",
            {
                "counterparty_id": "cp_delmar",
                "net_amount": "10.00",
                "description": "x",
                "due_in_days": 1,
            },
            "not a customer",
        ),
        (
            "RUN_SCENARIO",
            {"scenario": {"draw_credit_line": {"amount": "500000.00", "on": "2026-11-01"}}},
            "available",
        ),
        ("FLAG_ANOMALY", {"transaction_ids": ["txn_9999"]}, "does not exist"),
    ],
)
def test_invalid_payload_references(
    snapshot: LedgerSnapshot, kind: str, payload: dict[str, Any], error: str
) -> None:
    assert any(error in e for e in resolve(snapshot, kind, payload).errors)
