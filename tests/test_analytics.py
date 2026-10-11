"""Phase 5: cash-flow radar, scenarios, financing matcher and financial analysis."""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from ledgerguard.analytics import analysis, financing, radar
from ledgerguard.config import Policy
from ledgerguard.facts import (
    ANOMALIES,
    CASH_PROJECTION,
    FINANCIAL_SUMMARY,
    FINANCING_OPTIONS,
    OPEN_INVOICES,
    SCENARIO,
    tool_facts,
)
from ledgerguard.proposer.allowlist import plan_turn
from ledgerguard.verifier.pipeline import TurnContext, Verification, Verifier
from ledgerguard.verifier.seal import Sealer
from ledgerguard.verifier.snapshot import LedgerSnapshot

from .conftest import intent_json, keyed

LATE = {"type": "customer_pays_late", "counterparty_id": "cp_brennan", "days": 30}
DRAW = {"type": "draw_credit_line", "amount": "100000.00", "on": "2026-11-20"}


def alerts(snapshot: LedgerSnapshot, policy: Policy) -> list[radar.Alert]:
    r = policy.rules
    return radar.compute_alerts(
        snapshot,
        radar.baseline(snapshot),
        r.protection_band,
        r.payroll_margin_warning,
        r.concentration_warning_share,
    )


def simulate(snapshot: LedgerSnapshot, policy: Policy, scenario: dict[str, Any]) -> Any:
    r = policy.rules
    return radar.simulate(
        snapshot,
        scenario,
        r.protection_band,
        r.payroll_margin_warning,
        r.concentration_warning_share,
    )


def verify(
    snapshot: LedgerSnapshot,
    policy: Policy,
    kind: str,
    payload: dict[str, Any],
    claims: list[dict[str, Any]],
    tools: tuple[str, ...],
) -> Verification:
    raw = intent_json(kind, payload, claims, "Stub.")
    return Verifier(Sealer(bytes(32)), policy).verify(raw, snapshot, TurnContext(tools))


# ---- radar


def test_alerts_are_ranked_by_severity_and_each_names_its_rows(
    snapshot: LedgerSnapshot, policy: Policy
) -> None:
    found = alerts(snapshot, policy)
    assert [a.id for a in found[:2]] == ["shortfall", "tax.obl_0008"]
    assert all(a.rows for a in found)
    tax = found[1]
    assert tax.high and tax.lead_days == 53 and "€16,850.00" in tax.headline
    payroll = next(a for a in found if a.kind == "PAYROLL_PRESSURE")
    assert payroll.severity == "medium" and "€12,427.03 to spare" in payroll.headline


def test_alerts_cover_overdue_disputed_and_concentration(
    snapshot: LedgerSnapshot, policy: Policy
) -> None:
    kinds = {a.kind for a in alerts(snapshot, policy)}
    assert {"OVERDUE", "DISPUTED", "CONCENTRATION"} <= kinds


# ---- scenarios


def test_a_late_customer_deepens_the_low_and_raises_new_alerts(
    snapshot: LedgerSnapshot, policy: Policy
) -> None:
    result = simulate(snapshot, policy, LATE)
    low = result.lowest()
    assert (low.index, low.closing) == (11, Decimal("-424047.56"))
    assert [a.id for a in result.new_alerts] == ["tax.obl_0004", "payroll.pay_0027"]


def test_drawing_the_credit_line_clears_the_payroll_alert_and_charges_interest(
    snapshot: LedgerSnapshot, policy: Policy
) -> None:
    result = simulate(snapshot, policy, DRAW)
    assert [a.id for a in result.cleared_alerts] == ["payroll.pay_0026"]
    low = result.lowest()
    before = result.lowest("before")
    # Drawing 100,000 lifts the low by less than 100,000: interest is charged.
    assert low.index == 7 and Decimal(0) < low.closing - before.closing < Decimal("100000.00")


def test_a_scenario_never_changes_the_ledger(snapshot: LedgerSnapshot, policy: Policy) -> None:
    before = [w.closing for w in snapshot.weeks]
    simulate(snapshot, policy, LATE)
    assert [w.closing for w in snapshot.weeks] == before


@pytest.mark.parametrize(
    "scenario",
    [
        {**LATE, "counterparty_id": "cp_nobody"},
        {"type": "delay_payable", "invoice_id": "inv_0620", "days": 10},
    ],
)
def test_a_scenario_on_rows_that_do_not_fit_is_refused(
    snapshot: LedgerSnapshot, policy: Policy, scenario: dict[str, Any]
) -> None:
    with pytest.raises(ValueError):
        simulate(snapshot, policy, scenario)
    v = verify(
        snapshot,
        policy,
        "RUN_SCENARIO",
        {"scenario": keyed(scenario)},
        [],
        (CASH_PROJECTION, SCENARIO),
    )
    assert v.outcome == "rejected" and "INVALID_PAYLOAD_REFERENCE" in v.receipt.risk_flags


# ---- financing


def test_the_gap_is_the_shortfall_rounded_up(snapshot: LedgerSnapshot) -> None:
    gap = financing.find_gap(snapshot)
    assert gap is not None
    assert (gap.amount, str(gap.need_by), str(gap.until), gap.lead_days) == (
        Decimal("220000.00"),
        "2026-11-23",
        "2026-12-13",
        38,
    )


def test_options_are_ranked_with_total_cost_in_euros(snapshot: LedgerSnapshot) -> None:
    result = financing.match(snapshot).as_dict()
    ranked = [o for o in result["options"] if o["eligible"]]
    assert ranked[0]["product_id"] == "fin_cl_01"
    assert (ranked[0]["total_cost"], ranked[0]["amount"]) == ("512.05", "100000.00")
    assert result["synthetic_catalogue"] is True
    scores = [Decimal(o["score"]) for o in ranked]
    assert scores == sorted(scores, reverse=True)


def test_ineligible_products_say_why(snapshot: LedgerSnapshot) -> None:
    out = {
        o["product_id"]: o["reasons"]
        for o in financing.match(snapshot).as_dict()["options"]
        if not o["eligible"]
    }
    assert set(out) == {"fin_sc_02", "fin_tl_04"}
    assert "working capital" in out["fin_tl_04"][0]


def test_weights_change_the_ranking(snapshot: LedgerSnapshot) -> None:
    cover_first = financing.match(snapshot, {"coverage": Decimal(10), "cost": Decimal(0)})
    assert Decimal(cover_first.options[0].amount) == Decimal("220000.00")


@pytest.mark.parametrize("weights", [{"luck": Decimal(1)}, {"cost": Decimal(-1)}])
def test_bad_weights_are_refused(weights: dict[str, Decimal]) -> None:
    with pytest.raises(ValueError):
        financing.normalise(weights)


# ---- analysis


def test_metrics_match_hand_checked_values(snapshot: LedgerSnapshot) -> None:
    m = analysis.metrics(snapshot)
    assert m["gross_margin"].value == Decimal("27.17")
    assert m["net_margin"].value == Decimal("1.52")
    assert (m["dso"].value, m["dpo"].value) == (Decimal("50.4"), Decimal("51.8"))
    assert m["cash_conversion_cycle"].value == Decimal("-1.4")
    assert all(metric.method for metric in m.values())


def test_anomalies_find_the_planted_outlier_and_duplicate(snapshot: LedgerSnapshot) -> None:
    ids = [a.id for a in analysis.anomalies(snapshot)]
    assert "anom.txn_0869" in ids and "anom.dup.inv_0641.inv_0377" in ids


def test_mad_z_is_none_without_spread() -> None:
    assert analysis.mad_z([Decimal(5)] * 20, Decimal(9)) is None
    values = [Decimal(v) for v in (10, 11, 9, 10, 12, 8, 10)]
    z = analysis.mad_z(values, Decimal(40))
    assert z is not None and z > 5


# ---- facts and the verifier


def test_a_correct_financing_cost_verifies(snapshot: LedgerSnapshot, policy: Policy) -> None:
    v = verify(
        snapshot,
        policy,
        "RANK_FINANCING",
        {"needed_by_week": 6},
        [
            {
                "statement": "The credit line costs €512.05 for the gap.",
                "fact_id": "fin.fin_cl_01.total_cost",
                "figure": "512.05",
            }
        ],
        (CASH_PROJECTION, FINANCING_OPTIONS),
    )
    assert v.outcome == "verified", v.receipt.risk_flags
    assert v.presented["claims"][-1]["status"] == "verified"


def test_a_wrong_financing_cost_is_corrected(snapshot: LedgerSnapshot, policy: Policy) -> None:
    v = verify(
        snapshot,
        policy,
        "RANK_FINANCING",
        {"needed_by_week": 6},
        [
            {
                "statement": "The credit line costs €212.05 for the gap.",
                "fact_id": "fin.fin_cl_01.total_cost",
                "figure": "212.05",
            }
        ],
        (CASH_PROJECTION, FINANCING_OPTIONS),
    )
    assert "FIGURE_MISMATCH" in v.receipt.risk_flags
    assert "€512.05" in v.presented["claims"][-1]["statement"]


def test_a_true_number_quoted_for_the_wrong_fact_is_corrected(
    snapshot: LedgerSnapshot, policy: Policy
) -> None:
    """Week 5's closing is a verified number, but not the cash on hand."""
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
        (CASH_PROJECTION,),
    )
    assert "€129,120.63" in v.presented["claims"][-1]["statement"]


def test_citing_an_ineligible_product_is_not_a_cost_claim(
    snapshot: LedgerSnapshot, policy: Policy
) -> None:
    v = verify(
        snapshot,
        policy,
        "RANK_FINANCING",
        {"needed_by_week": 6},
        [
            {"statement": "The credit line costs €512.05.", "fact_id": "fin.fin_cl_01.total_cost"},
            {
                "statement": "Equipment finance is not possible.",
                "fact_id": "fin.fin_tl_04.ineligible",
            },
        ],
        (CASH_PROJECTION, FINANCING_OPTIONS),
    )
    assert v.outcome == "verified", v.receipt.risk_flags


def test_a_scenario_result_is_recomputed_and_mandatory(
    snapshot: LedgerSnapshot, policy: Policy
) -> None:
    facts = {f.id: f for f in tool_facts(snapshot, SCENARIO, policy, {"scenario": LATE})}
    assert facts["scn.result"].mandatory and facts["scn.result"].figure == Decimal("-424047.56")
    v = verify(
        snapshot,
        policy,
        "RUN_SCENARIO",
        {"scenario": keyed(LATE)},
        [{"statement": "Week 11 gets worse.", "fact_id": "scn.w11.closing"}],
        (CASH_PROJECTION, SCENARIO),
    )
    assert "MATERIAL_OMISSION" in v.receipt.risk_flags
    inserted = [m["fact_id"] for m in v.receipt.material_omissions]
    assert "scn.result" in inserted


def test_summary_and_anomaly_facts_cite_rows(snapshot: LedgerSnapshot, policy: Policy) -> None:
    summary = {f.id: f for f in tool_facts(snapshot, FINANCIAL_SUMMARY, policy)}
    assert summary["ana.gross_margin"].figure == Decimal("27.17")
    anomaly = {f.id: f for f in tool_facts(snapshot, ANOMALIES, policy)}
    assert anomaly["anom.txn_0869"].source_row_ids == ("txn_0869",)


# ---- routes


@pytest.mark.parametrize(
    "request_text, tool",
    [
        ("What are my financing options for the gap?", FINANCING_OPTIONS),
        ("How healthy is our margin?", FINANCIAL_SUMMARY),
        ("Anything unusual in the books?", ANOMALIES),
        ("What if we delay the supplier invoice by 30 days?", OPEN_INVOICES),
    ],
)
def test_routes_bring_the_matching_tool(request_text: str, tool: str) -> None:
    assert tool in plan_turn(request_text).tools


# ---- endpoints


@pytest.fixture
def api(fresh_db: Path, tmp_path: Path) -> Any:
    from fastapi.testclient import TestClient

    from ledgerguard.api.app import create_app
    from ledgerguard.config import Settings, load_model_config, load_policy

    root = Path(__file__).resolve().parents[1]
    settings = Settings(
        db_path=fresh_db,
        config_dir=root / "config",
        policy=load_policy(root / "config" / "policy.yaml"),
        model=load_model_config(root / "config" / "model.yaml"),
        key_path=tmp_path / "k.key",
    )
    with TestClient(create_app(settings)) as c:
        yield c


def test_projection_endpoint(api: Any) -> None:
    body = api.get("/api/cashflow/projection", params={"weeks": 8}).json()
    assert len(body["weeks"]) == 8 and body["weeks"][6]["closing"] == "-219676.22"
    assert api.get("/api/cashflow/projection", params={"weeks": 14}).status_code == 422


def test_alerts_endpoint(api: Any) -> None:
    body = api.get("/api/alerts").json()
    assert body["alerts"][0]["kind"] == "SHORTFALL"


def test_scenario_endpoint(api: Any) -> None:
    body = api.post("/api/cashflow/scenario", json={"scenario": DRAW}).json()
    assert [a["id"] for a in body["cleared_alerts"]] == ["payroll.pay_0026"]
    bad = api.post("/api/cashflow/scenario", json={"scenario": {"type": "rob_a_bank"}})
    assert bad.status_code == 422


def test_financing_endpoint_takes_decimal_strings_only(api: Any) -> None:
    ok = api.post("/api/financing/match", json={"weights": {"cost": "1", "coverage": "3"}})
    assert ok.status_code == 200 and ok.json()["gap"]["amount"] == "220000.00"
    assert api.post("/api/financing/match", json={"weights": {"cost": 0.5}}).status_code == 422
    assert api.post("/api/financing/match", json={"weights": {"luck": "1"}}).status_code == 422


def test_analysis_endpoint(api: Any) -> None:
    body = api.get("/api/analysis").json()
    names = [m["name"] for m in body["metrics"]]
    assert "gross_margin" in names and body["anomalies"]
