"""The JSON schema handed to the model runtime to constrain its output.

Ollama turns this schema into a grammar, so the model cannot emit a token that
breaks it. That removes malformed output as a failure class. It does not make
the output correct, which is still the verifier's job.

The schema is written by hand rather than exported from the Pydantic models.
The export would allow money as a JSON number, which the parser refuses.
``tests/test_proposer_schema.py`` keeps the two in step. The schema is built per
turn from the kinds the turn allows, so the grammar also enforces the allowlist.

The grammar shapes what the model is able to say, so it is part of what has to
be tested. Two properties of Ollama's grammar were measured on 2026-10-11 with
``scripts/probe_grammar.py``:

- Object properties come out in a fixed order: required properties first, then
  optional ones, each group in alphabetical order. A model that writes a later
  property first can never go back for an earlier optional one.
- With one branch per alternative under ``anyOf``, that ordering decides which
  branch the model can finish. Told outright to write a late-paying customer
  scenario, the model could only produce the stress-test branch.

So no generated schema uses ``anyOf``, and the prompt shows every example in
the order the turn's grammar enforces, through ``grammar_order``.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from ledgerguard.domain.enums import ActionKind
from ledgerguard.verifier.intent import MAX_CLAIMS, Formula

MONEY = {"type": "string", "pattern": "^-?[0-9]{1,12}(\\.[0-9]{1,2})?$"}
FIGURE = {"type": "string", "pattern": "^-?[0-9]{1,12}(\\.[0-9]{1,6})?$"}
DATE = {"type": "string", "pattern": "^20[0-9]{2}-[01][0-9]-[0-3][0-9]$"}
ROW_ID = {"type": "string", "pattern": "^[a-z]{2,4}_[A-Za-z0-9_]{1,40}$"}
FACT_ID = {"type": "string", "pattern": "^[a-z][A-Za-z0-9_.]{1,60}$"}
TEXT = {"type": "string", "minLength": 1, "maxLength": 600}


def _object(properties: dict[str, Any], required: Iterable[str] | None = None) -> dict[str, Any]:
    return {
        "type": "object",
        "properties": properties,
        "required": list(properties if required is None else required),
        "additionalProperties": False,
    }


def _ids(max_items: int) -> dict[str, Any]:
    return {"type": "array", "items": ROW_ID, "minItems": 1, "maxItems": max_items}


# One key per scenario type, each holding that type's fields, all required.
# Neither a branch per type nor a flat object with optional arguments survives
# the grammar's fixed property order. See verifier.intent.ScenarioChoice.
_DAYS = {"type": "integer", "minimum": 1, "maximum": 180}
SCENARIO = _object(
    {
        "customer_pays_late": _object({"counterparty_id": ROW_ID, "days": _DAYS}),
        "delay_payable": _object({"days": _DAYS, "invoice_id": ROW_ID}),
        "draw_credit_line": _object({"amount": MONEY, "on": DATE}),
        "contracted_inflows_only": _object({}),
    },
    required=[],
)

PAYLOADS: dict[ActionKind, dict[str, Any]] = {
    ActionKind.ANSWER_QUERY: _object({"topic": TEXT}),
    ActionKind.DRAFT_FORECAST: _object({"weeks": {"type": "integer", "minimum": 1, "maximum": 13}}),
    ActionKind.RUN_SCENARIO: _object({"scenario": SCENARIO}),
    ActionKind.FLAG_ANOMALY: _object({"transaction_ids": _ids(50)}),
    ActionKind.DRAFT_INVOICE: _object(
        {
            "counterparty_id": ROW_ID,
            "net_amount": MONEY,
            "description": TEXT,
            "due_in_days": {"type": "integer", "minimum": 0, "maximum": 180},
        }
    ),
    ActionKind.RECORD_PAYMENT: _object(
        {"invoice_id": ROW_ID, "amount": MONEY, "received_on": DATE}, required=["invoice_id"]
    ),
    ActionKind.MARK_DISPUTED: _object({"invoice_id": ROW_ID, "reason": TEXT}),
    ActionKind.RANK_FINANCING: _object(
        {"needed_by_week": {"type": "integer", "minimum": 1, "maximum": 13}}, required=[]
    ),
    ActionKind.INITIATE_PAYMENT: _object(
        {"invoice_ids": _ids(200), "pay_on": DATE}, required=["invoice_ids"]
    ),
    ActionKind.ACCEPT_FINANCING_OFFER: _object({"product_id": ROW_ID, "amount": MONEY}),
}

DERIVATION = _object(
    {
        "formula": {"enum": [f.value for f in Formula]},
        "operands": _ids(200),
        "params": {"type": "object", "additionalProperties": {"type": "string"}},
    },
    required=["formula", "operands"],
)

CLAIM = _object(
    {
        "statement": TEXT,
        "fact_id": FACT_ID,
        "figure": FIGURE,
        "derivation": DERIVATION,
        "source_row_ids": {"type": "array", "items": ROW_ID, "maxItems": 200},
    },
    required=["statement"],
)


def _merged_payload(kinds: list[ActionKind]) -> dict[str, Any]:
    """Every allowed kind's payload properties in one object, none of them required.

    A field shared by two kinds has the same schema in both, which is asserted
    here so a future payload cannot quietly loosen another.
    """
    properties: dict[str, Any] = {}
    for kind in kinds:
        for name, spec in PAYLOADS[kind]["properties"].items():
            if properties.setdefault(name, spec) != spec:
                raise ValueError(f"payload field {name} has two different schemas")
    return _object(properties, required=[])


def intent_schema(allowed: Iterable[ActionKind]) -> dict[str, Any]:
    """The schema for one turn. Only the allowed kinds are expressible.

    With one kind, the payload is that kind's exact schema. With several, the
    kind is an enum and the payload is the merged object from
    ``_merged_payload``. The grammar then cannot tie a payload to its kind, so
    the strict parser does, and a mismatch is retried like any malformed output.
    """
    kinds = sorted(set(allowed), key=lambda k: k.value)
    if not kinds:
        raise ValueError("a turn must allow at least one action kind")
    one = len(kinds) == 1
    return _object(
        {
            "kind": {"type": "string", "enum": [k.value for k in kinds]},
            "payload": PAYLOADS[kinds[0]] if one else _merged_payload(kinds),
            "claims": {"type": "array", "items": CLAIM, "maxItems": MAX_CLAIMS},
            "rationale": TEXT,
        }
    )


def grammar_order(value: Any, schema: dict[str, Any]) -> Any:
    """``value`` with every object's keys in the order the grammar enforces.

    Required properties come first and optional ones after, each group sorted.
    The prompt shows examples this way, because a model copies the key order it
    is shown and the grammar would otherwise trap it.
    """
    if isinstance(value, list):
        items = schema.get("items", {})
        return [grammar_order(v, items) for v in value]
    if not isinstance(value, dict) or schema.get("type") != "object":
        return value
    props: dict[str, Any] = schema.get("properties", {})
    required = set(schema.get("required", []))
    extra = schema.get("additionalProperties")
    order = sorted(value, key=lambda k: (k not in required, k))
    return {
        k: grammar_order(value[k], props.get(k, extra if isinstance(extra, dict) else {}))
        for k in order
    }
