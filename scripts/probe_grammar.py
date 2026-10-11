"""Probe how the local runtime's schema grammar shapes what the model can say.

    uv run python scripts/probe_grammar.py OUT_DIR

Each probe sends one short request with a JSON schema as Ollama's `format`
and saves the exact request and the raw response under OUT_DIR. Temperature
is zero, so a probe gives the same answer when run again on the same model.

1. ordering: properties declared as zebra, apple, mango, all required. Does
   the output keep that order or sort it?
2. required_first: zed required, beta and alpha optional, asked for in the
   order beta, alpha, zed. Where do required properties go?
3. scenario_<shape>: a late-paying customer, asked for outright, under each of
   the three scenario schemas the project has used: branched, flat with a
   type field, and keyed by type, which is the current one.
4. payable_natural_order and payable_grammar_order: a supplier-invoice delay
   under the flat schema, asked for with invoice_id before days, then in the
   grammar's order.
5. after_example_<shape>: the situation that broke scenario 3 on 2026-10-11.
   The model is shown a supplier-invoice delay as an example and asked for a
   late-paying customer, under the flat and the keyed schema.
6. kind_<KIND>: the turn schema allowing every action kind, with the model told
   outright which kind and payload to write, in the grammar's order. A grammar
   that biased the kind choice or the payload would show up here.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.request
from pathlib import Path
from typing import Any

from ledgerguard.domain.enums import ActionKind
from ledgerguard.proposer import schema as S

URL = "http://localhost:11434/api/chat"
MODEL = "qwen2.5-7b-instruct"

BRANCHED_SCENARIOS: list[dict[str, Any]] = [
    S._object(
        {
            "type": {"const": "customer_pays_late"},
            "counterparty_id": S.ROW_ID,
            "days": {"type": "integer", "minimum": 1, "maximum": 180},
        }
    ),
    S._object(
        {
            "type": {"const": "delay_payable"},
            "invoice_id": S.ROW_ID,
            "days": {"type": "integer", "minimum": 1, "maximum": 180},
        }
    ),
    S._object({"type": {"const": "draw_credit_line"}, "amount": S.MONEY, "on": S.DATE}),
    S._object({"type": {"const": "contracted_inflows_only"}}),
]

_DAYS = {"type": "integer", "minimum": 1, "maximum": 180}
FLAT_SCENARIO = S._object(
    {
        "type": {
            "type": "string",
            "enum": [
                "customer_pays_late",
                "delay_payable",
                "draw_credit_line",
                "contracted_inflows_only",
            ],
        },
        "counterparty_id": S.ROW_ID,
        "invoice_id": S.ROW_ID,
        "days": _DAYS,
        "amount": S.MONEY,
        "on": S.DATE,
    },
    required=["type"],
)

SCENARIO_ASK = (
    "Return JSON with scenario type customer_pays_late, counterparty_id cp_brennan, days 30."
)

# One payload per kind that the model is asked to copy. None of them names a figure.
KIND_PAYLOADS: dict[ActionKind, dict[str, Any]] = {
    ActionKind.ANSWER_QUERY: {"topic": "cash"},
    ActionKind.DRAFT_FORECAST: {"weeks": 13},
    ActionKind.RUN_SCENARIO: {
        "scenario": {"delay_payable": {"days": 14, "invoice_id": "inv_0652"}}
    },
    ActionKind.FLAG_ANOMALY: {"transaction_ids": ["txn_0869"]},
    ActionKind.DRAFT_INVOICE: {
        "counterparty_id": "cp_glenmore",
        "net_amount": "1200.00",
        "description": "Spare door gaskets",
        "due_in_days": 30,
    },
    ActionKind.RECORD_PAYMENT: {"invoice_id": "inv_0412"},
    ActionKind.MARK_DISPUTED: {"invoice_id": "inv_0412", "reason": "Short delivery."},
    ActionKind.RANK_FINANCING: {"needed_by_week": 7},
    ActionKind.INITIATE_PAYMENT: {"invoice_ids": ["inv_0652"]},
    ActionKind.ACCEPT_FINANCING_OFFER: {"product_id": "fin_cl_01", "amount": "50000.00"},
}


def ask(content: str, fmt: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    body = {
        "model": MODEL,
        "stream": False,
        "format": fmt,
        "options": {"temperature": 0},
        "messages": [{"role": "user", "content": content}],
    }
    request = urllib.request.Request(
        URL, json.dumps(body).encode(), {"Content-Type": "application/json"}
    )
    with urllib.request.urlopen(request, timeout=600) as response:
        return body, json.loads(response.read())


def save(out: Path, name: str, body: dict[str, Any], reply: dict[str, Any]) -> str:
    content: str = reply["message"]["content"]
    (out / f"{name}.json").write_text(
        json.dumps({"request": body, "response": reply}, indent=1, ensure_ascii=False)
    )
    return content


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("out", type=Path)
    args = parser.parse_args(argv)
    out: Path = args.out
    out.mkdir(parents=True, exist_ok=True)
    summary: dict[str, Any] = {"model": MODEL, "ran_at": time.strftime("%Y-%m-%dT%H:%M:%S%z")}

    ordering = {
        "type": "object",
        "properties": {
            "zebra": {"type": "string"},
            "apple": {"type": "string"},
            "mango": {"type": "string"},
        },
        "required": ["zebra", "apple", "mango"],
        "additionalProperties": False,
    }
    body, reply = ask(
        "Return JSON with zebra 'z', apple 'a' and mango 'm', in that order.", ordering
    )
    summary["ordering"] = list(json.loads(save(out, "ordering", body, reply)))

    required_first = {
        "type": "object",
        "properties": {
            "beta": {"type": "string"},
            "alpha": {"type": "string"},
            "zed": {"type": "string"},
        },
        "required": ["zed"],
        "additionalProperties": False,
    }
    body, reply = ask(
        "Return JSON with beta 'b', alpha 'a' and zed 'z', in that order.", required_first
    )
    summary["required_first"] = json.loads(save(out, "required_first", body, reply))

    shapes = {
        "branched": {"anyOf": BRANCHED_SCENARIOS},
        "flat": FLAT_SCENARIO,
        "keyed": S.SCENARIO,
    }
    keyed_ask = "Return JSON with scenario customer_pays_late: counterparty_id cp_brennan, days 30."
    for shape, scenario in shapes.items():
        ask_text = keyed_ask if shape == "keyed" else SCENARIO_ASK
        body, reply = ask(ask_text, S._object({"scenario": scenario}))
        summary[f"scenario_{shape}"] = json.loads(save(out, f"scenario_{shape}", body, reply))[
            "scenario"
        ]

    payable = {"type": "delay_payable", "invoice_id": "inv_0652", "days": 14}
    for name, shown in (
        ("payable_natural_order", payable),
        ("payable_grammar_order", S.grammar_order(payable, FLAT_SCENARIO)),
    ):
        body, reply = ask(
            f"Return JSON with scenario {json.dumps(shown)}.",
            S._object({"scenario": FLAT_SCENARIO}),
        )
        summary[name] = json.loads(save(out, name, body, reply))["scenario"]

    examples = {
        "flat": {"type": "delay_payable", "days": 14, "invoice_id": "inv_0652"},
        "keyed": {"delay_payable": {"days": 14, "invoice_id": "inv_0652"}},
    }
    for shape, example in examples.items():
        body, reply = ask(
            f'Example of the format: {{"scenario": {json.dumps(example)}}}\n'
            "Now return the scenario where customer cp_brennan pays 30 days late.",
            S._object({"scenario": shapes[shape]}),
        )
        summary[f"after_example_{shape}"] = json.loads(
            save(out, f"after_example_{shape}", body, reply)
        )["scenario"]

    turn_schema = S.intent_schema(list(ActionKind))
    summary["turn_schema_branches"] = "anyOf" in json.dumps(turn_schema)
    kinds: dict[str, dict[str, Any]] = {}
    for kind, payload in KIND_PAYLOADS.items():
        intent = {"kind": kind.value, "payload": payload, "claims": [], "rationale": "probe"}
        shown = S.grammar_order(intent, turn_schema)
        body, reply = ask(f"Return exactly this ActionIntent: {json.dumps(shown)}", turn_schema)
        text = save(out, f"kind_{kind.value}", body, reply)
        try:
            got = json.loads(text)
        except json.JSONDecodeError:
            got = {}
        kinds[kind.value] = {
            "kind": got.get("kind", "<missing>"),
            "payload_as_asked": got.get("payload") == payload,
        }
    summary["kinds"] = kinds
    (out / "summary.json").write_text(json.dumps(summary, indent=1))
    print(json.dumps(summary, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
