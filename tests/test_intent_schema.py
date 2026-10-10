"""Stage 1: the ActionIntent contract is strict and never repaired."""

from __future__ import annotations

import json

import pytest

from ledgerguard.domain.enums import ActionKind
from ledgerguard.verifier.intent import MAX_RAW_BYTES, InitiatePayment, parse_intent

from .conftest import intent_json

GOOD_CLAIM = {
    "statement": "Invoice inv_0412 is outstanding",
    "figure": "46854.00",
    "derivation": {"formula": "INVOICE_OUTSTANDING", "operands": ["inv_0412"]},
    "source_row_ids": ["inv_0412"],
}


def errors(raw: str | bytes) -> list[str]:
    result = parse_intent(raw)
    assert result.intent is None
    return [f"{e.location}: {e.message}" for e in result.errors]


def test_valid_intent_parses() -> None:
    result = parse_intent(intent_json("RECORD_PAYMENT", {"invoice_id": "inv_0412"}, [GOOD_CLAIM]))
    assert result.errors == ()
    assert result.intent is not None
    assert result.intent.kind is ActionKind.RECORD_PAYMENT
    assert str(result.intent.claims[0].figure) == "46854.00"


def test_every_kind_has_a_payload_model() -> None:
    payloads = {
        "ANSWER_QUERY": {"topic": "cash"},
        "DRAFT_FORECAST": {"weeks": 13},
        "RUN_SCENARIO": {
            "scenario": {"type": "customer_pays_late", "counterparty_id": "cp_brennan", "days": 30}
        },
        "FLAG_ANOMALY": {"transaction_ids": ["txn_0869"]},
        "DRAFT_INVOICE": {
            "counterparty_id": "cp_brennan",
            "net_amount": "100.00",
            "description": "x",
            "due_in_days": 30,
        },
        "RECORD_PAYMENT": {"invoice_id": "inv_0412"},
        "MARK_DISPUTED": {"invoice_id": "inv_0412", "reason": "x"},
        "RANK_FINANCING": {"needed_by_week": 7},
        "INITIATE_PAYMENT": {"invoice_ids": ["inv_0377"]},
        "ACCEPT_FINANCING_OFFER": {"product_id": "fin_if_01", "amount": "50000.00"},
    }
    assert set(payloads) == {k.value for k in ActionKind}
    for kind, payload in payloads.items():
        assert parse_intent(intent_json(kind, payload)).intent is not None, kind


@pytest.mark.parametrize(
    "raw, fragment",
    [
        (intent_json("TRANSFER_ALL_FUNDS", {}), "kind"),
        (intent_json("RECORD_PAYMENT", {"invoice_id": "inv_0412"}, override=True), "override"),
        (
            intent_json("RECORD_PAYMENT", {"invoice_id": "inv_0412", "bypass_rules": True}),
            "payload.bypass_rules",
        ),
        (intent_json("RECORD_PAYMENT", {"invoice_id": 412}), "payload.invoice_id"),
        (
            intent_json("RECORD_PAYMENT", {"invoice_id": "inv_0412", "amount": 46854}),
            "payload.amount",
        ),
        (
            intent_json("INITIATE_PAYMENT", {"invoice_ids": ["inv_0377"], "amount": "4312.50"}),
            "payload.amount",
        ),
        (intent_json("INITIATE_PAYMENT", {"invoice_ids": []}), "payload.invoice_ids"),
        (
            intent_json("RECORD_PAYMENT", {"invoice_id": "inv_0412; DROP TABLE invoice"}),
            "payload.invoice_id",
        ),
        (intent_json("DRAFT_FORECAST", {"weeks": 52}), "payload.weeks"),
        (intent_json("DRAFT_FORECAST", {"weeks": "13"}), "payload.weeks"),
        (
            intent_json("ANSWER_QUERY", {"topic": "x"}, [{**GOOD_CLAIM, "figure": 46854}]),
            "claims.0.figure",
        ),
        (
            intent_json(
                "ANSWER_QUERY",
                {"topic": "x"},
                [{**GOOD_CLAIM, "derivation": {"formula": "TRUST_ME", "operands": ["x_1"]}}],
            ),
            "claims.0.derivation.formula",
        ),
        (
            json.dumps({"kind": "ANSWER_QUERY", "payload": {"topic": "x"}, "claims": []}),
            "rationale",
        ),
    ],
)
def test_malformed_intents_are_rejected_not_repaired(raw: str, fragment: str) -> None:
    found = errors(raw)
    assert any(e.startswith(fragment) for e in found), found


def test_bare_json_numbers_are_refused_before_parsing() -> None:
    raw = intent_json("ANSWER_QUERY", {"topic": "x"}).replace(
        '"claims": []',
        '"claims": [{"statement": "s", "figure": 18400.0, "source_row_ids": ["inv_0412"]}]',
    )
    assert any("bare JSON number" in e for e in errors(raw))


@pytest.mark.parametrize("raw", ["", "not json", "[]", '"a string"', b"\xff\xfe", "{" * 10])
def test_garbage_is_rejected(raw: str | bytes) -> None:
    assert errors(raw)


def test_oversized_output_is_rejected() -> None:
    raw = intent_json("ANSWER_QUERY", {"topic": "x"}, rationale="a" * 1000)
    assert errors(raw + " " * MAX_RAW_BYTES)


def test_model_supplied_tier_is_dropped_and_recorded() -> None:
    result = parse_intent(intent_json("INITIATE_PAYMENT", {"invoice_ids": ["inv_0377"]}, tier="T0"))
    assert result.intent is not None
    assert result.ignored_tier == "T0"
    assert "tier" not in result.intent.model_dump()


def test_payment_payload_cannot_carry_an_amount() -> None:
    assert "amount" not in InitiatePayment.model_fields


def test_uncited_claims_pass_the_schema_and_are_left_to_provenance() -> None:
    claim = {"statement": "Cash is fine", "source_row_ids": []}
    assert parse_intent(intent_json("ANSWER_QUERY", {"topic": "x"}, [claim])).intent is not None
