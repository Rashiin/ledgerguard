"""The JSON schema handed to the model runtime to constrain its output.

Ollama turns this schema into a grammar, so the model cannot emit a token that
breaks it. That removes malformed output as a failure class. It does not make
the output correct, which is still the verifier's job.

The schema is written by hand rather than exported from the Pydantic models.
The export would allow money as a JSON number, which the parser refuses, and it
would not tie each payload to its kind. ``tests/test_proposer_schema.py`` keeps
the two in step. The schema is built per turn from the kinds the turn allows,
so the grammar also enforces the allowlist.
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
FACT_ID = {"type": "string", "pattern": "^[a-z][a-z0-9_.]{1,60}$"}
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


SCENARIOS = [
    _object(
        {
            "type": {"const": "customer_pays_late"},
            "counterparty_id": ROW_ID,
            "days": {"type": "integer", "minimum": 1, "maximum": 180},
        }
    ),
    _object(
        {
            "type": {"const": "delay_payable"},
            "invoice_id": ROW_ID,
            "days": {"type": "integer", "minimum": 1, "maximum": 180},
        }
    ),
    _object({"type": {"const": "draw_credit_line"}, "amount": MONEY, "on": DATE}),
    _object({"type": {"const": "contracted_inflows_only"}}),
]

PAYLOADS: dict[ActionKind, dict[str, Any]] = {
    ActionKind.ANSWER_QUERY: _object({"topic": TEXT}),
    ActionKind.DRAFT_FORECAST: _object({"weeks": {"type": "integer", "minimum": 1, "maximum": 13}}),
    ActionKind.RUN_SCENARIO: _object({"scenario": {"anyOf": SCENARIOS}}),
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


def intent_schema(allowed: Iterable[ActionKind]) -> dict[str, Any]:
    """The schema for one turn. Only the allowed kinds are expressible."""
    kinds = sorted(set(allowed), key=lambda k: k.value)
    if not kinds:
        raise ValueError("a turn must allow at least one action kind")
    branches = [
        _object(
            {
                "kind": {"const": kind.value},
                "payload": PAYLOADS[kind],
                "claims": {"type": "array", "items": CLAIM, "maxItems": MAX_CLAIMS},
                "rationale": TEXT,
            }
        )
        for kind in kinds
    ]
    return branches[0] if len(branches) == 1 else {"anyOf": branches}
