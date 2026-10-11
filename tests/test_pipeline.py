"""The verifier plane end to end, with a stubbed proposer.

The stub proposer is a function that returns raw JSON text. Everything after
it is the real verifier, the real Executor and a private copy of the ledger.
"""

from __future__ import annotations

import json
import sqlite3
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from ledgerguard.audit.store import load_events, verify_stored
from ledgerguard.db.engine import make_readonly_sessionmaker
from ledgerguard.db.models import ActionIntentRecord, Invoice, Transaction
from ledgerguard.domain.enums import Actor, InvoiceStatus
from ledgerguard.executor.executor import ExecutionRefused
from ledgerguard.service import CopilotService
from ledgerguard.verifier.policy import FINANCING_DISCLOSURE, GUARANTEE_MARKER
from ledgerguard.verifier.text import UNVERIFIED

from .conftest import Clock, intent_json


def stub_proposer(
    kind: str,
    payload: dict[str, Any],
    claims: list[dict[str, Any]] | None = None,
    rationale: str = "Stub proposer output.",
    **extra: Any,
) -> str:
    return intent_json(kind, payload, claims, rationale, **extra)


def invoice(db: Path, invoice_id: str) -> Invoice:
    with make_readonly_sessionmaker(db)() as s:
        row = s.get(Invoice, invoice_id)
        assert row is not None
        s.expunge(row)
        return row


def chain(db: Path) -> tuple[bool, list[str]]:
    with make_readonly_sessionmaker(db)() as s:
        return verify_stored(s).intact, [e.event_type for e in load_events(s)]


# ---- every turn gets a receipt; rejected output changes nothing


def test_malformed_output_is_rejected_visibly(copilot: CopilotService, fresh_db: Path) -> None:
    turn = copilot.submit(
        '{"kind": "INITIATE_PAYMENT", "payload": {"invoice_ids": '
        '["inv_0377"]}, "rationale": "pay", "claims": [], "urgent": true}',
        "conv",
    )
    assert turn.outcome == "rejected"
    assert turn.presented["message"].startswith("The agent produced a malformed action")
    assert turn.receipt["schema_errors"][0]["location"] == "urgent"
    assert "MALFORMED_ACTION" in turn.receipt["risk_flags"]
    assert turn.execution is None
    assert invoice(fresh_db, "inv_0377").status is InvoiceStatus.OVERDUE
    with make_readonly_sessionmaker(fresh_db)() as s:
        record = s.get(ActionIntentRecord, turn.intent_id)
        assert record is not None and record.kind is None and record.raw_output
    assert chain(fresh_db) == (True, ["intent.rejected"])


def test_receipt_is_retrievable_and_matches_the_audit_digest(
    copilot: CopilotService, fresh_db: Path
) -> None:
    turn = copilot.submit(stub_proposer("DRAFT_FORECAST", {"weeks": 13}), "conv")
    stored = copilot.receipt(turn.receipt["receipt_id"])
    assert stored == turn.receipt
    with make_readonly_sessionmaker(fresh_db)() as s:
        event = next(e for e in load_events(s) if e.event_type == "intent.verified")
    from ledgerguard.verifier.receipt import TrustReceipt

    assert event.payload["receipt_sha256"] == TrustReceipt(**stored).digest()
    assert event.seq == stored["audit_seq"]


# ---- figures: recomputed values win, unsupported content is visibly removed


def test_wrong_figure_is_corrected_everywhere_the_user_sees_it(copilot: CopilotService) -> None:
    claims = [
        {
            "statement": "Week 7 closes at €18,400.00, which is tight.",
            "figure": "18400.00",
            "derivation": {
                "formula": "PROJECTED_CLOSING",
                "operands": ["inv_0665", "pay_0026"],
                "params": {"week": "7", "band": "all"},
            },
            "source_row_ids": ["inv_0665", "pay_0026"],
        }
    ]
    turn = copilot.submit(
        stub_proposer(
            "ANSWER_QUERY",
            {"topic": "week 7"},
            claims,
            "Week 7 is €18,400 after the €142,500 Halvorsen bill. This will fix itself.",
        ),
        "conv",
    )
    assert turn.outcome == "verified" and turn.status == "executed"
    claim = turn.presented["claims"][0]
    assert claim["status"] == "corrected"
    assert claim["model_figure"] == "18400.00"
    assert claim["figure"] == "-219676.22"
    assert "18,400" not in claim["statement"] and "-€219,676.22" in claim["statement"]
    rationale = turn.presented["rationale"]
    assert "18,400" not in rationale and "-€219,676.22" in rationale
    assert UNVERIFIED in rationale  # the uncited €142,500
    assert GUARANTEE_MARKER in rationale
    rc = turn.receipt["recomputations"][0]
    assert rc["match"] is False and rc["recomputed"] == "-219676.22"
    flags = set(turn.receipt["risk_flags"])
    assert {"FIGURE_MISMATCH", "TEXT_FIGURE_MASKED", "GUARANTEED_OUTCOME_LANGUAGE"} <= flags


def test_uncited_claim_is_replaced_by_a_visible_marker(copilot: CopilotService) -> None:
    claims = [{"statement": "Brennan always pays eventually.", "source_row_ids": []}]
    turn = copilot.submit(stub_proposer("ANSWER_QUERY", {"topic": "b"}, claims), "conv")
    assert turn.presented["claims"][0]["statement"].startswith(
        "[unsupported claim removed by verifier"
    )
    assert "UNSUPPORTED_CLAIM_REMOVED" in turn.receipt["risk_flags"]


def test_model_tier_is_ignored(copilot: CopilotService) -> None:
    turn = copilot.submit(
        stub_proposer("INITIATE_PAYMENT", {"invoice_ids": ["inv_0377"]}, tier="T0"), "conv"
    )
    assert turn.presented["tier"] == "T3"
    assert "MODEL_TIER_IGNORED" in turn.receipt["risk_flags"]


def test_invalid_payload_reference_is_rejected(copilot: CopilotService, fresh_db: Path) -> None:
    turn = copilot.submit(stub_proposer("INITIATE_PAYMENT", {"invoice_ids": ["inv_0412"]}), "conv")
    assert turn.outcome == "rejected"
    assert "INVALID_PAYLOAD_REFERENCE" in turn.receipt["risk_flags"]


# ---- scenario 7: record a receipt, T2 with an approval diff


def test_scenario_7_record_payment_needs_approval_then_applies(
    copilot: CopilotService, fresh_db: Path
) -> None:
    turn = copilot.submit(stub_proposer("RECORD_PAYMENT", {"invoice_id": "inv_0412"}), "conv")
    assert (turn.outcome, turn.status, turn.presented["tier"]) == (
        "verified",
        "pending_approval",
        "T2",
    )
    assert turn.receipt["payload_figures"]["recorded_amount"] == "46854.00"
    assert invoice(fresh_db, "inv_0412").status is InvoiceStatus.OVERDUE  # nothing yet

    pending = copilot.pending()
    assert [p["intent_id"] for p in pending] == [turn.intent_id]
    # The inbox diff runs the real handler and rolls it back: nothing is written.
    diff = copilot.preview(turn.intent_id)
    assert invoice(fresh_db, "inv_0412").status is InvoiceStatus.OVERDUE
    assert chain(fresh_db) == (True, ["intent.verified"])
    with pytest.raises(ExecutionRefused, match="cannot approve"):
        copilot.approve(turn.intent_id, Actor.LLM)

    result = copilot.approve(turn.intent_id, Actor.OPERATOR)
    assert diff == result.changes
    with pytest.raises(ExecutionRefused, match="not waiting"):
        copilot.preview(turn.intent_id)
    fields = {(c["table"], c["field"]): c for c in result.changes}
    assert fields[("invoice", "status")]["before"] == "overdue"
    assert fields[("invoice", "status")]["after"] == "paid"
    assert fields[("invoice", "paid_amount")]["after"] == "46854.00"
    paid = invoice(fresh_db, "inv_0412")
    assert paid.status is InvoiceStatus.PAID and paid.paid_amount == paid.gross_amount
    with make_readonly_sessionmaker(fresh_db)() as s:
        txn = s.query(Transaction).filter_by(invoice_id="inv_0412").one()
        assert txn.direction.value == "in" and str(txn.amount) == "46854.00"
    assert copilot.pending() == []
    # The refused LLM approval attempt is on the record too.
    assert chain(fresh_db) == (True, ["intent.verified", "action.refused", "action.executed"])


# ---- scenario 8: duplicate supplier invoice, T3 with DuplicatePayment


def test_scenario_8_duplicate_payment_is_t3_and_gated(
    copilot: CopilotService, fresh_db: Path, clock: Clock, policy: Any
) -> None:
    turn = copilot.submit(stub_proposer("INITIATE_PAYMENT", {"invoice_ids": ["inv_0377"]}), "conv")
    assert turn.presented["tier"] == "T3" and turn.status == "pending_approval"
    assert "DUPLICATE_PAYMENT" in turn.receipt["risk_flags"]
    dup = next(r for r in turn.receipt["rules_evaluated"] if r["rule"] == "DuplicatePayment")
    assert dup["result"] == "WARN" and "inv_0641" in dup["rows"]
    phrase = turn.receipt["approval"]["confirmation_phrase"]
    assert phrase == f"CONFIRM {turn.intent_id}"

    with pytest.raises(ExecutionRefused, match="typed confirmation"):
        copilot.approve(turn.intent_id, Actor.OPERATOR)
    with pytest.raises(ExecutionRefused, match="cool-off"):
        copilot.approve(turn.intent_id, Actor.OPERATOR, phrase)
    clock.advance(policy.t3.cool_off_seconds)
    result = copilot.approve(turn.intent_id, Actor.OWNER, phrase)
    assert any(c["after"] == "paid" for c in result.changes)
    assert invoice(fresh_db, "inv_0377").status is InvoiceStatus.PAID
    with pytest.raises(ExecutionRefused, match="at most once"):
        copilot.approve(turn.intent_id, Actor.OWNER, phrase)


# ---- scenario 9: PayrollProtection blocks, and no approval can override it


def _all_open_payables(db: Path) -> list[str]:
    with make_readonly_sessionmaker(db)() as s:
        return [
            i.id
            for i in s.query(Invoice).filter(
                Invoice.type == "payable", Invoice.status.in_(["sent", "overdue"])
            )
        ]


def test_scenario_9_payroll_protection_blocks(copilot: CopilotService, fresh_db: Path) -> None:
    turn = copilot.submit(
        stub_proposer("INITIATE_PAYMENT", {"invoice_ids": _all_open_payables(fresh_db)}), "conv"
    )
    assert turn.outcome == "blocked"
    blocked = {b["rule"] for b in turn.presented["blocked_by"]}
    assert "PayrollProtection" in blocked
    reason = next(
        b["reason"] for b in turn.presented["blocked_by"] if b["rule"] == "PayrollProtection"
    )
    assert "pay_0025" in reason and "short by" in reason
    assert turn.receipt["approval"]["status"] == "not_applicable"
    with make_readonly_sessionmaker(fresh_db)() as s:
        record = s.get(ActionIntentRecord, turn.intent_id)
        assert record is not None and record.sealed_action is None
        assert record.status.value == "rejected"
    for approver in (Actor.OPERATOR, Actor.OWNER):
        with pytest.raises(ExecutionRefused):
            copilot.approve(turn.intent_id, approver, f"CONFIRM {turn.intent_id}")
    assert all(
        i.status is not InvoiceStatus.PAID
        for i in (invoice(fresh_db, x) for x in _all_open_payables(fresh_db))
    )


def test_block_is_absolute_even_if_a_sealed_blocked_action_is_forged_into_the_queue(
    copilot: CopilotService, fresh_db: Path, clock: Clock, policy: Any
) -> None:
    """Someone with file access edits a pending action to drop its blocking rule or change
    what it pays. The seal no longer matches, and the Executor refuses."""
    turn = copilot.submit(stub_proposer("INITIATE_PAYMENT", {"invoice_ids": ["inv_0377"]}), "conv")
    conn = sqlite3.connect(fresh_db)
    (sealed,) = conn.execute(
        "SELECT sealed_action FROM action_intent WHERE id = ?", (turn.intent_id,)
    ).fetchone()
    doc = json.loads(sealed)
    doc["payload"]["resolved"]["payments"][0]["amount"] = "1.00"
    conn.execute(
        "UPDATE action_intent SET sealed_action = ? WHERE id = ?", (json.dumps(doc), turn.intent_id)
    )
    conn.commit()
    conn.close()
    clock.advance(policy.t3.cool_off_seconds)
    with pytest.raises(ExecutionRefused, match="seal"):
        copilot.approve(turn.intent_id, Actor.OPERATOR, f"CONFIRM {turn.intent_id}")
    assert invoice(fresh_db, "inv_0377").status is InvoiceStatus.OVERDUE


def test_disputed_invoice_settlement_is_blocked(copilot: CopilotService) -> None:
    turn = copilot.submit(stub_proposer("RECORD_PAYMENT", {"invoice_id": "inv_0620"}), "conv")
    assert turn.outcome == "blocked"
    assert [b["rule"] for b in turn.presented["blocked_by"]] == ["DisputedInvoiceLock"]


def test_declined_action_is_closed(copilot: CopilotService, fresh_db: Path) -> None:
    turn = copilot.submit(
        stub_proposer("MARK_DISPUTED", {"invoice_id": "inv_0412", "reason": "short delivery"}),
        "conv",
    )
    copilot.decline(turn.intent_id, Actor.OPERATOR, "customer confirmed delivery")
    with pytest.raises(ExecutionRefused, match="declined"):
        copilot.approve(turn.intent_id, Actor.OPERATOR)
    assert invoice(fresh_db, "inv_0412").status is InvoiceStatus.OVERDUE


# ---- policy: financing


def test_financing_without_total_cost_is_blocked_and_disclosed(copilot: CopilotService) -> None:
    claims = [
        {
            "statement": "Selective invoice finance costs 1.25% a month.",
            "source_row_ids": ["fin_if_01"],
        }
    ]
    turn = copilot.submit(
        stub_proposer(
            "RANK_FINANCING",
            {"needed_by_week": 7},
            claims,
            "Factoring is risk-free and guaranteed to help.",
        ),
        "conv",
    )
    assert turn.outcome == "blocked"
    assert "POLICY_VIOLATION" in turn.receipt["risk_flags"]
    assert any("total cost of capital" in b["reason"] for b in turn.presented["blocked_by"])
    assert turn.presented["disclosures"] == [FINANCING_DISCLOSURE]
    assert "risk-free" not in turn.presented["rationale"]


def test_accepting_an_offer_is_t3_and_carries_the_disclosure(copilot: CopilotService) -> None:
    turn = copilot.submit(
        stub_proposer("ACCEPT_FINANCING_OFFER", {"product_id": "fin_cl_01", "amount": "1000.00"}),
        "conv",
    )
    assert turn.presented["tier"] == "T3"
    assert FINANCING_DISCLOSURE in turn.receipt["disclosures"]


# ---- approval-time re-check: a BLOCK cannot be reached by approving things one by one


def test_two_payments_fine_alone_cannot_jointly_starve_payroll(
    copilot: CopilotService, fresh_db: Path, clock: Clock, policy: Any, snapshot: Any
) -> None:
    """Each payment passes PayrollProtection when verified. Approving the first changes the
    ledger, so the second, re-checked at approval time, is now blocked and closed."""
    from datetime import timedelta

    from ledgerguard.verifier.snapshot import Band

    first, second = "inv_0652", "inv_0672"  # due 30 Oct and 11 Nov: paying now pulls them forward
    a1 = snapshot.invoices[first].gross_amount
    a2 = snapshot.invoices[second].gross_amount
    pay_day = snapshot.payroll_runs["pay_0025"].pay_date
    days = (pay_day - timedelta(days=policy.rules.payroll_lead_days), pay_day - timedelta(days=1))
    cover = min(snapshot.projected_cash_on(d, Band.WITH_EXPECTED) for d in days)
    need = cover - max(a1, a2) - Decimal("5000.00")  # each alone leaves >= need ...
    assert cover - a1 - a2 < need  # ... both together do not
    conn = sqlite3.connect(fresh_db)
    conn.execute(
        "UPDATE payroll_run SET employer_cost_total = ? WHERE id = 'pay_0025'", (int(need * 100),)
    )
    # Isolate PayrollProtection: a larger payroll would otherwise also squeeze the VAT reserve.
    conn.execute("UPDATE obligation SET amount = 100000 WHERE kind = 'tax'")
    conn.commit()
    conn.close()

    t1 = copilot.submit(stub_proposer("INITIATE_PAYMENT", {"invoice_ids": [first]}), "c")
    t2 = copilot.submit(stub_proposer("INITIATE_PAYMENT", {"invoice_ids": [second]}), "c")
    assert t1.status == t2.status == "pending_approval", (
        t1.presented.get("blocked_by"),
        t2.presented.get("blocked_by"),
    )
    clock.advance(policy.t3.cool_off_seconds)
    copilot.approve(t1.intent_id, Actor.OPERATOR, f"CONFIRM {t1.intent_id}")
    with pytest.raises(ExecutionRefused, match="PayrollProtection"):
        copilot.approve(t2.intent_id, Actor.OPERATOR, f"CONFIRM {t2.intent_id}")
    assert invoice(fresh_db, second).status is not InvoiceStatus.PAID
    with make_readonly_sessionmaker(fresh_db)() as s:
        record = s.get(ActionIntentRecord, t2.intent_id)
        assert record is not None and record.status.value == "rejected"
    assert chain(fresh_db)[0]


def test_paid_invoice_cannot_be_paid_again_through_a_stale_approval(
    copilot: CopilotService, fresh_db: Path, clock: Clock, policy: Any
) -> None:
    t1 = copilot.submit(stub_proposer("INITIATE_PAYMENT", {"invoice_ids": ["inv_0652"]}), "c")
    t2 = copilot.submit(stub_proposer("INITIATE_PAYMENT", {"invoice_ids": ["inv_0652"]}), "c")
    clock.advance(policy.t3.cool_off_seconds)
    copilot.approve(t1.intent_id, Actor.OPERATOR, f"CONFIRM {t1.intent_id}")
    with pytest.raises(ExecutionRefused, match="nothing to pay"):
        copilot.approve(t2.intent_id, Actor.OPERATOR, f"CONFIRM {t2.intent_id}")


def test_t1_draft_invoice_executes_automatically(copilot: CopilotService, fresh_db: Path) -> None:
    turn = copilot.submit(
        stub_proposer(
            "DRAFT_INVOICE",
            {
                "counterparty_id": "cp_glenmore",
                "net_amount": "1200.00",
                "description": "Spare gaskets",
                "due_in_days": 30,
            },
        ),
        "conv",
    )
    assert (turn.presented["tier"], turn.status) == ("T1", "executed")
    assert turn.execution is not None
    new_id = turn.execution.changes[0]["id"]
    draft = invoice(fresh_db, new_id)
    assert draft.status is InvoiceStatus.DRAFT
    assert (str(draft.net_amount), str(draft.tax_amount)) == ("1200.00", "276.00")


def test_scenario_7_variant_large_receipt_is_t2_and_recordable(
    copilot: CopilotService, fresh_db: Path
) -> None:
    """Brennan's €227,550 refit receipt is above the outbound ceiling and must still record."""
    turn = copilot.submit(stub_proposer("RECORD_PAYMENT", {"invoice_id": "inv_0678"}), "conv")
    assert (turn.outcome, turn.presented["tier"]) == ("verified", "T2")
    copilot.approve(turn.intent_id, Actor.OPERATOR)
    assert invoice(fresh_db, "inv_0678").status is InvoiceStatus.PAID


def test_receipt_above_what_is_owed_is_rejected(copilot: CopilotService) -> None:
    turn = copilot.submit(
        stub_proposer("RECORD_PAYMENT", {"invoice_id": "inv_0412", "amount": "46854.01"}), "c"
    )
    assert turn.outcome == "rejected"


def test_accepting_financing_states_the_cost_of_the_amount_accepted(
    copilot: CopilotService,
) -> None:
    """The verifier states the cost of exactly what is confirmed. The model never saw that
    figure, so its absence from the answer is not an omission."""
    turn = copilot.submit(
        stub_proposer("ACCEPT_FINANCING_OFFER", {"product_id": "fin_if_01", "amount": "150000.00"}),
        "conv",
    )
    first = turn.presented["claims"][0]
    assert first["fact_id"] == "fin.accept.total_cost" and first["mandatory"] is True
    assert "€150,000.00" in first["statement"]
    assert "MATERIAL_OMISSION" not in turn.receipt["risk_flags"]
    assert "POLICY_VIOLATION" not in turn.receipt["risk_flags"]
    assert turn.status == "pending_approval" and turn.presented["tier"] == "T3"
