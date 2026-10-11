"""The constrained-decoding schema, the prompt examples and the Pydantic contract agree."""

from __future__ import annotations

import json
import re
from typing import Any

import pytest

from ledgerguard.domain.enums import ActionKind
from ledgerguard.proposer import schema as S
from ledgerguard.proposer.prompt import EXAMPLES, system_prompt
from ledgerguard.verifier import intent as I


def test_every_kind_has_a_schema_and_an_example() -> None:
    assert set(S.PAYLOADS) == set(ActionKind) == set(EXAMPLES)


@pytest.mark.parametrize("kind", list(ActionKind))
def test_schema_payload_fields_match_the_model(kind: ActionKind) -> None:
    assert set(S.PAYLOADS[kind]["properties"]) == set(I.PAYLOADS[kind].model_fields)


def test_claim_schema_matches_the_model() -> None:
    assert set(S.CLAIM["properties"]) == set(I.Claim.model_fields)
    assert set(S.DERIVATION["properties"]["formula"]["enum"]) == {f.value for f in I.Formula}


@pytest.mark.parametrize("kind", list(ActionKind))
def test_every_example_passes_the_real_parser(kind: ActionKind) -> None:
    result = I.parse_intent(json.dumps(EXAMPLES[kind]))
    assert result.errors == (), result.errors
    assert result.intent is not None and result.intent.kind is kind


def _conforms(value: Any, schema: dict[str, Any]) -> bool:
    """A small validator for the schema subset we use, so no new dependency is needed."""
    if "anyOf" in schema:
        return any(_conforms(value, s) for s in schema["anyOf"])
    if "const" in schema:
        return bool(value == schema["const"])
    if "enum" in schema:
        return value in schema["enum"]
    kind = schema.get("type")
    if kind == "object":
        if not isinstance(value, dict):
            return False
        props = schema["properties"]
        if any(k not in props for k in value) and schema.get("additionalProperties") is False:
            return False
        if any(r not in value for r in schema.get("required", [])):
            return False
        extra = schema.get("additionalProperties")
        return all(
            _conforms(v, props[k])
            if k in props
            else (not isinstance(extra, dict) or _conforms(v, extra))
            for k, v in value.items()
        )
    if kind == "array":
        return (
            isinstance(value, list)
            and len(value) >= schema.get("minItems", 0)
            and all(_conforms(v, schema["items"]) for v in value)
        )
    if kind == "string":
        return isinstance(value, str) and (
            "pattern" not in schema or re.fullmatch(schema["pattern"], value) is not None
        )
    if kind == "integer":
        return isinstance(value, int) and schema.get("minimum", value) <= value <= schema.get(
            "maximum", value
        )
    raise AssertionError(f"unhandled schema {schema}")


@pytest.mark.parametrize("kind", list(ActionKind))
def test_every_example_fits_the_constrained_schema(kind: ActionKind) -> None:
    assert _conforms(EXAMPLES[kind], S.intent_schema([kind]))


def test_the_schema_only_admits_allowed_kinds() -> None:
    schema = S.intent_schema([ActionKind.ANSWER_QUERY, ActionKind.DRAFT_FORECAST])
    assert _conforms(EXAMPLES[ActionKind.ANSWER_QUERY], schema)
    assert not _conforms(EXAMPLES[ActionKind.INITIATE_PAYMENT], schema)


@pytest.mark.parametrize(
    "bad",
    [
        {"amount": 4312.5},
        {"amount": "4312.505"},
        {"amount": "about 4k"},
    ],
)
def test_the_schema_refuses_bad_money(bad: dict[str, Any]) -> None:
    payload = {"product_id": "fin_cl_01", **bad}
    intent = {**EXAMPLES[ActionKind.ACCEPT_FINANCING_OFFER], "payload": payload}
    assert not _conforms(intent, S.intent_schema([ActionKind.ACCEPT_FINANCING_OFFER]))


def test_payment_schema_has_no_amount_field() -> None:
    assert "amount" not in S.PAYLOADS[ActionKind.INITIATE_PAYMENT]["properties"]


def test_prompt_lists_only_allowed_examples() -> None:
    prompt = system_prompt([ActionKind.ANSWER_QUERY], "EUR")
    assert '"kind": "ANSWER_QUERY"' in prompt and "INITIATE_PAYMENT" not in prompt
    assert "€" in prompt


def test_claim_rejects_fact_and_derivation_together() -> None:
    raw = json.dumps(
        {
            **EXAMPLES[ActionKind.ANSWER_QUERY],
            "claims": [
                {
                    "statement": "s",
                    "fact_id": "cash.on_hand",
                    "derivation": {"formula": "CASH_ON_HAND", "operands": ["acc_operating"]},
                }
            ],
        }
    )
    assert I.parse_intent(raw).intent is None


def _all_schemas() -> list[dict[str, Any]]:
    """Every grammar the proposer can hand the runtime: one per kind, every pair,
    every route's plan, and all kinds together."""
    from itertools import combinations

    from ledgerguard.proposer.allowlist import ROUTES

    kinds = list(ActionKind)
    sets = [[k] for k in kinds] + [list(p) for p in combinations(kinds, 2)] + [kinds]
    sets += [[ActionKind.ANSWER_QUERY, *r.kinds] for r in ROUTES]
    return [S.intent_schema(s) for s in sets]


def _keywords(node: Any) -> set[str]:
    if isinstance(node, dict):
        return set(node) | {k for v in node.values() for k in _keywords(v)}
    if isinstance(node, list):
        return {k for v in node for k in _keywords(v)}
    return set()


def test_no_generated_grammar_branches() -> None:
    """Ollama orders properties alphabetically, so a branch per alternative lets
    the ordering pick the branch. No grammar may branch anywhere."""
    for schema in _all_schemas():
        assert not _keywords(schema) & {"anyOf", "oneOf", "allOf", "if", "not"}


def _objects(node: Any) -> list[dict[str, Any]]:
    if isinstance(node, dict):
        found = [node] if node.get("type") == "object" else []
        return found + [o for v in node.values() for o in _objects(v)]
    if isinstance(node, list):
        return [o for v in node for o in _objects(v)]
    return []


def test_the_scenario_is_keyed_by_type_with_every_argument_required() -> None:
    """A flat scenario with optional arguments let the grammar trap the model: having
    written days, it could no longer write counterparty_id, which sorts first."""
    scenario = S.intent_schema([ActionKind.RUN_SCENARIO])["properties"]["payload"]["properties"][
        "scenario"
    ]
    assert scenario["required"] == []
    assert set(scenario["properties"]) == {
        "customer_pays_late",
        "delay_payable",
        "draw_credit_line",
        "contracted_inflows_only",
    }
    for args in scenario["properties"].values():
        assert args["required"] == list(args["properties"])


def test_optional_fields_never_depend_on_a_choice_made_in_the_same_object() -> None:
    """The trap needs an object with a required choice and two or more optional
    fields that depend on it: having written one, the model may be past the other.
    The merged turn payload is the one exception, which D-041 explains."""
    allowed_mixed = {id(S._merged_payload(list(ActionKind)))}
    for schema in _all_schemas():
        payload = schema["properties"]["payload"]
        allowed_mixed.add(id(payload))
        for obj in _objects(schema):
            if id(obj) in allowed_mixed:
                continue
            props = obj.get("properties", {})
            optional = [k for k in props if k not in obj.get("required", [])]
            enums = [k for k, v in props.items() if "enum" in v and k in obj.get("required", [])]
            assert not (len(optional) >= 2 and enums), (obj, optional, enums)


def test_grammar_order_puts_required_first_then_sorts() -> None:
    schema = S._object(
        {"zed": {"type": "string"}, "beta": {"type": "string"}, "alpha": {"type": "string"}},
        required=["zed"],
    )
    value = {"beta": "b", "alpha": "a", "zed": "z"}
    assert list(S.grammar_order(value, schema)) == ["zed", "alpha", "beta"]


@pytest.mark.parametrize("kind", list(ActionKind))
def test_prompt_examples_are_shown_in_grammar_order(kind: ActionKind) -> None:
    """The model copies the order it is shown. An example in another order would
    teach it a sequence the grammar cannot finish."""
    prompt = system_prompt([kind], "EUR")
    shown = json.dumps(S.grammar_order(EXAMPLES[kind], S.intent_schema([kind])), ensure_ascii=False)
    assert shown in prompt
    assert shown.index('"claims"') < shown.index('"kind"') < shown.index('"payload"')


def test_a_mixed_turn_shows_the_merged_order() -> None:
    kinds = [ActionKind.ANSWER_QUERY, ActionKind.RECORD_PAYMENT]
    schema = S.intent_schema(kinds)
    assert schema["properties"]["payload"]["required"] == []
    example = S.grammar_order(EXAMPLES[ActionKind.RECORD_PAYMENT], schema)
    assert example["kind"] == "RECORD_PAYMENT"
    assert json.dumps(example) in system_prompt(kinds, "EUR")
