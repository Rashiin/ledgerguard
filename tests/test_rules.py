"""Stage 3: every rule, with a test proving its BLOCK case."""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal
from typing import Any

import pytest

from ledgerguard.config import Policy
from ledgerguard.domain.enums import ActionKind, InvoiceStatus, Tier
from ledgerguard.rules import REGISTRY, Outcome, RuleContext, RuleResult, evaluate
from ledgerguard.rules import core as rules
from ledgerguard.verifier.intent import parse_intent
from ledgerguard.verifier.recompute import resolve_payload
from ledgerguard.verifier.snapshot import LedgerSnapshot
from ledgerguard.verifier.tiers import ALWAYS_T3, assign_tier

from .conftest import intent_json


def ctx(
    snapshot: LedgerSnapshot,
    policy: Policy,
    kind: str,
    payload: dict[str, Any],
    cited: frozenset[str] = frozenset(),
) -> RuleContext:
    intent = parse_intent(intent_json(kind, payload)).intent
    assert intent is not None
    resolution = resolve_payload(snapshot, intent, set())
    assert resolution.errors == [], resolution.errors
    return RuleContext(intent, resolution, snapshot, policy, cited)


def pay(*ids: str) -> tuple[str, dict[str, Any]]:
    return "INITIATE_PAYMENT", {"invoice_ids": list(ids)}


def results(c: RuleContext) -> dict[str, RuleResult]:
    return {r.rule: r for r in evaluate(c)}


QUERY = ("ANSWER_QUERY", {"topic": "cash"})


def test_registry_has_the_eight_spec_rules() -> None:
    names = [r.__name__ for r in REGISTRY]
    assert names == [
        "sufficient_balance",
        "amount_ceiling",
        "duplicate_payment",
        "counterparty_whitelist",
        "payroll_protection",
        "tax_reserve_floor",
        "disputed_invoice_lock",
        "concentration_warning",
    ]


def test_small_clean_payment_passes_every_blocking_rule(
    snapshot: LedgerSnapshot, policy: Policy
) -> None:
    # Due within two weeks anyway, so paying it now moves no cash past a tax date.
    soon = snapshot.as_of + timedelta(days=14)
    clean = min(
        (
            i
            for i in snapshot.invoices.values()
            if i.type.value == "payable"
            and i.status is InvoiceStatus.SENT
            and i.due_on <= soon
            and i.counterparty_id != "cp_delmar"
        ),
        key=lambda i: i.gross_amount,
    )
    found = results(ctx(snapshot, policy, *pay(clean.id)))
    assert all(r.result is not Outcome.BLOCK for r in found.values()), found


# ---- SufficientBalance


def test_sufficient_balance_blocks_overdrawing(snapshot: LedgerSnapshot, policy: Policy) -> None:
    r = rules.sufficient_balance(ctx(snapshot, policy, *pay("inv_0665")))
    assert r.result is Outcome.BLOCK and "floor" in r.reason
    assert r.rows == ("acc_operating",)


def test_sufficient_balance_respects_the_floor_exactly(
    snapshot: LedgerSnapshot, policy: Policy
) -> None:
    snapshot.balances["acc_operating"] = Decimal("4312.50") + Decimal("4999.99")
    assert rules.sufficient_balance(ctx(snapshot, policy, *pay("inv_0377"))).result is Outcome.BLOCK
    snapshot.balances["acc_operating"] = Decimal("4312.50") + Decimal("5000.00")
    assert rules.sufficient_balance(ctx(snapshot, policy, *pay("inv_0377"))).result is Outcome.PASS


def test_sufficient_balance_ignores_non_payments(snapshot: LedgerSnapshot, policy: Policy) -> None:
    assert rules.sufficient_balance(ctx(snapshot, policy, *QUERY)).result is Outcome.PASS


# ---- AmountCeiling


def test_amount_ceiling_blocks_outbound_money_above_the_ceiling(
    snapshot: LedgerSnapshot, policy: Policy
) -> None:
    policy = policy.model_copy(update={"outbound_ceiling": Decimal("100000.00")})
    r = rules.amount_ceiling(ctx(snapshot, policy, *pay("inv_0665")))
    assert r.result is Outcome.BLOCK and "ceiling for money leaving" in r.reason


def test_outbound_ceiling_is_inclusive(snapshot: LedgerSnapshot, policy: Policy) -> None:
    exact = policy.model_copy(update={"outbound_ceiling": Decimal("142500.00")})
    assert rules.amount_ceiling(ctx(snapshot, exact, *pay("inv_0665"))).result is Outcome.PASS


def test_large_receipt_is_not_capped(snapshot: LedgerSnapshot, policy: Policy) -> None:
    """Recording money received moves nothing out, so no ceiling applies."""
    receipt = ctx(snapshot, policy, "RECORD_PAYMENT", {"invoice_id": "inv_0678"})
    assert receipt.resolution.amount == Decimal("227550.00")
    assert receipt.resolution.amount > policy.outbound_ceiling
    assert rules.amount_ceiling(receipt).result is Outcome.PASS
    assert all(r.result is not Outcome.BLOCK for r in evaluate(receipt))


def test_drafts_and_scenarios_are_not_capped(snapshot: LedgerSnapshot, policy: Policy) -> None:
    draft = ctx(
        snapshot,
        policy,
        "DRAFT_INVOICE",
        {
            "counterparty_id": "cp_brennan",
            "net_amount": "900000.00",
            "description": "x",
            "due_in_days": 30,
        },
    )
    scenario = ctx(
        snapshot,
        policy,
        "RUN_SCENARIO",
        {"scenario": {"type": "draw_credit_line", "amount": "90000.00", "on": "2026-11-20"}},
    )
    for c in (draft, scenario):
        assert rules.amount_ceiling(c).result is Outcome.PASS


# ---- DuplicatePayment


def test_duplicate_payment_flags_the_planted_pair(snapshot: LedgerSnapshot, policy: Policy) -> None:
    r = rules.duplicate_payment(ctx(snapshot, policy, *pay("inv_0377")))
    assert r.result is Outcome.WARN
    assert "inv_0641" in r.rows and "inv_0377" in r.rows
    assert "DUPLICATE_PAYMENT" in r.flags


def test_duplicate_within_one_batch(snapshot: LedgerSnapshot, policy: Policy) -> None:
    twin = snapshot.invoices["inv_0377"]
    other = next(
        i
        for i in snapshot.invoices.values()
        if i.type.value == "payable" and i.status is InvoiceStatus.SENT and i.id != "inv_0377"
    )
    other.counterparty_id, other.gross_amount = twin.counterparty_id, twin.gross_amount
    r = rules.duplicate_payment(ctx(snapshot, policy, *pay("inv_0377", other.id)))
    assert r.result is Outcome.WARN and other.id in r.rows


def test_duplicate_payment_forces_human_approval(snapshot: LedgerSnapshot) -> None:
    for kind in ActionKind:
        assert assign_tier(kind, needs_human=True) in (Tier.T2, Tier.T3)


def test_duplicate_payment_is_a_flag_not_a_block(snapshot: LedgerSnapshot, policy: Policy) -> None:
    found = results(ctx(snapshot, policy, *pay("inv_0377")))
    assert all(r.result is not Outcome.BLOCK for r in found.values())


# ---- CounterpartyWhitelist


def test_whitelist_blocks_unlisted_payee(snapshot: LedgerSnapshot, policy: Policy) -> None:
    snapshot.counterparties["cp_delmar"].is_whitelisted = False
    r = rules.counterparty_whitelist(ctx(snapshot, policy, *pay("inv_0377")))
    assert r.result is Outcome.BLOCK and r.rows == ("cp_delmar",)


def test_whitelist_passes_listed_payee(snapshot: LedgerSnapshot, policy: Policy) -> None:
    assert (
        rules.counterparty_whitelist(ctx(snapshot, policy, *pay("inv_0377"))).result is Outcome.PASS
    )


# ---- PayrollProtection


def test_payroll_protection_blocks_payment_that_starves_payroll(
    snapshot: LedgerSnapshot, policy: Policy
) -> None:
    run = snapshot.payroll_runs["pay_0025"]
    run.employer_cost_total = Decimal("175000.00")  # nothing else trips; balance is fine
    r = rules.payroll_protection(ctx(snapshot, policy, *pay("inv_0377")))
    assert r.result is Outcome.BLOCK
    assert "pay_0025" in r.reason and "short by" in r.reason
    assert (
        results(ctx(snapshot, policy, *pay("inv_0377")))["SufficientBalance"].result is Outcome.PASS
    )


def test_payroll_protection_blocks_scenario_9(snapshot: LedgerSnapshot, policy: Policy) -> None:
    open_payables = [
        i.id
        for i in snapshot.invoices.values()
        if i.type.value == "payable" and i.status in (InvoiceStatus.SENT, InvoiceStatus.OVERDUE)
    ]
    r = rules.payroll_protection(ctx(snapshot, policy, *pay(*open_payables)))
    assert r.result is Outcome.BLOCK


def test_payroll_pressure_warns_without_blocking_non_payments(
    snapshot: LedgerSnapshot, policy: Policy
) -> None:
    snapshot.payroll_runs["pay_0025"].employer_cost_total = Decimal("900000.00")
    r = rules.payroll_protection(ctx(snapshot, policy, *QUERY))
    assert r.result is Outcome.WARN and "PAYROLL_PRESSURE" in r.flags


def test_payment_after_payday_is_not_judged_against_it(
    snapshot: LedgerSnapshot, policy: Policy
) -> None:
    snapshot.payroll_runs["pay_0025"].employer_cost_total = Decimal("900000.00")
    later = ctx(
        snapshot, policy, "INITIATE_PAYMENT", {"invoice_ids": ["inv_0377"], "pay_on": "2026-10-29"}
    )
    assert rules.payroll_protection(later).result is not Outcome.BLOCK


# ---- TaxReserveFloor


def test_tax_reserve_blocks_payment_into_ring_fenced_cash(
    snapshot: LedgerSnapshot, policy: Policy
) -> None:
    vat = next(o for o in snapshot.obligations.values() if o.kind.value == "tax")
    vat.amount = Decimal("400000.00")
    r = rules.tax_reserve_floor(ctx(snapshot, policy, *pay("inv_0377")))
    assert r.result is Outcome.BLOCK and vat.id in r.rows
    assert rules.tax_reserve_floor(ctx(snapshot, policy, *QUERY)).result is Outcome.WARN


def test_paying_early_into_the_vat_reserve_is_blocked(
    snapshot: LedgerSnapshot, policy: Policy
) -> None:
    """inv_0675 is due 29 Nov. Paying it today moves €28.8k ahead of the 23 Nov VAT return
    and leaves less than the VAT due: the reserve is ring-fenced."""
    assert snapshot.invoices["inv_0675"].due_on.isoformat() == "2026-11-29"
    r = rules.tax_reserve_floor(ctx(snapshot, policy, *pay("inv_0675")))
    assert r.result is Outcome.BLOCK and "VAT" in r.reason


def test_tax_reserve_passes_on_seed_data(snapshot: LedgerSnapshot, policy: Policy) -> None:
    assert rules.tax_reserve_floor(ctx(snapshot, policy, *pay("inv_0377"))).result is Outcome.PASS


# ---- DisputedInvoiceLock


def test_disputed_invoice_cannot_be_settled(snapshot: LedgerSnapshot, policy: Policy) -> None:
    r = rules.disputed_invoice_lock(
        ctx(snapshot, policy, "RECORD_PAYMENT", {"invoice_id": "inv_0620"})
    )
    assert r.result is Outcome.BLOCK and r.rows == ("inv_0620",)


def test_disputed_payable_cannot_be_paid(snapshot: LedgerSnapshot, policy: Policy) -> None:
    snapshot.invoices["inv_0377"].status = InvoiceStatus.DISPUTED
    r = rules.disputed_invoice_lock(ctx(snapshot, policy, *pay("inv_0377")))
    assert r.result is Outcome.BLOCK


# ---- ConcentrationWarning


def test_concentration_warns_about_brennan(snapshot: LedgerSnapshot, policy: Policy) -> None:
    r = rules.concentration_warning(
        ctx(snapshot, policy, "RECORD_PAYMENT", {"invoice_id": "inv_0412"})
    )
    assert r.result is Outcome.WARN and r.rows == ("cp_brennan",)


def test_concentration_never_blocks(snapshot: LedgerSnapshot, policy: Policy) -> None:
    for kind, payload in (
        QUERY,
        pay("inv_0377"),
        ("DRAFT_FORECAST", {"weeks": 13}),
        ("RECORD_PAYMENT", {"invoice_id": "inv_0412"}),
    ):
        assert (
            rules.concentration_warning(ctx(snapshot, policy, kind, payload)).result
            is not Outcome.BLOCK
        )


def test_an_advisory_rule_that_blocks_is_a_bug(
    snapshot: LedgerSnapshot, policy: Policy, monkeypatch: pytest.MonkeyPatch
) -> None:
    def rogue(_: RuleContext) -> RuleResult:
        return RuleResult("ConcentrationWarning", Outcome.BLOCK, "x")

    monkeypatch.setattr(rules, "REGISTRY", (rogue,))
    with pytest.raises(AssertionError, match="advisory"):
        rules.evaluate(ctx(snapshot, policy, *QUERY))


# ---- purity


def test_rules_do_not_mutate_the_snapshot(snapshot: LedgerSnapshot, policy: Policy) -> None:
    before = (
        dict(snapshot.balances),
        [(i.id, i.status, i.paid_amount) for i in snapshot.invoices.values()],
    )
    evaluate(ctx(snapshot, policy, *pay("inv_0377", "inv_0665")))
    after = (
        dict(snapshot.balances),
        [(i.id, i.status, i.paid_amount) for i in snapshot.invoices.values()],
    )
    assert before == after


# ---- tiers


@pytest.mark.parametrize("kind", sorted(ALWAYS_T3))
@pytest.mark.parametrize("needs_human", [False, True])
def test_payment_and_offer_acceptance_are_always_t3(kind: ActionKind, needs_human: bool) -> None:
    assert assign_tier(kind, needs_human) is Tier.T3


def test_tier_depends_on_kind_not_amount() -> None:
    assert assign_tier(ActionKind.ANSWER_QUERY, False) is Tier.T0
    assert assign_tier(ActionKind.DRAFT_INVOICE, False) is Tier.T1
    assert assign_tier(ActionKind.RECORD_PAYMENT, False) is Tier.T2
    assert assign_tier(ActionKind.RUN_SCENARIO, True) is Tier.T2
