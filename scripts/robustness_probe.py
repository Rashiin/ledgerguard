"""Measure how often the verifier has to correct, mask or remove a model figure.

    uv run python scripts/robustness_probe.py evidence/robustness

This is an experiment, not a product setting. The product and every recording
run at temperature 0. Here the same figure-heavy turns are sampled at a higher
temperature, with a different seed for each sample, so the model's figure
handling varies and the verifier's corrections can be counted as a rate rather
than an anecdote.

Each sample runs on a fresh copy of the seeded ledger through the real
orchestrator and verifier. Every raw model response is written next to the
verified result and its receipt, so any count in the summary can be checked
against the run that produced it.
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
import tempfile
import time
from collections import Counter
from pathlib import Path
from typing import Any

from ledgerguard.config import load_model_config, load_policy
from ledgerguard.db.engine import make_readonly_sessionmaker
from ledgerguard.executor.executor import Executor
from ledgerguard.proposer.client import LLMClient, LLMResponse, LocalClient
from ledgerguard.proposer.orchestrator import Orchestrator
from ledgerguard.service import CopilotService
from ledgerguard.verifier.pipeline import Verifier
from ledgerguard.verifier.seal import Sealer

ROOT = Path(__file__).resolve().parent.parent

# Three turns whose answers carry many figures: a forecast, a cost comparison and a
# payment whose total the model is tempted to add up itself.
TURNS = (
    ("s01_cash_quarter", "How is our cash looking for the next quarter?", 17),
    (
        "s05_factoring_vs_credit_line",
        "Compare the factoring option against the credit line over the real drawdown period",
        17,
    ),
    ("s09_pay_all_suppliers", "Pay all outstanding supplier invoices this week", 16),
)


class Capturing(LLMClient):
    """Keeps every raw exchange of one sample."""

    def __init__(self, inner: LLMClient) -> None:
        self._inner = inner
        self.exchanges: list[dict[str, Any]] = []

    def complete(self, messages: list[dict[str, str]], schema: dict[str, Any]) -> LLMResponse:
        reply = self._inner.complete(messages, schema)
        self.exchanges.append(
            {"messages": messages, "response": reply.content, "elapsed_ms": reply.elapsed_ms}
        )
        return reply


def interventions(receipt: dict[str, Any], presented: dict[str, Any]) -> dict[str, int]:
    """What the verifier did to the model's figures in one turn."""
    recomps = receipt["recomputations"]
    fixes = receipt["text_corrections"]
    return {
        "figures_checked": sum(1 for r in recomps if r["model_figure"] is not None),
        "figure_corrected": sum(1 for r in recomps if r["status"] == "mismatch"),
        "figure_not_recomputable": sum(1 for r in recomps if r["status"] == "not_recomputable"),
        "text_figure_corrected": sum(1 for f in fixes if "corrected to recomputed" in f["reason"]),
        "text_figure_masked": sum(
            1 for f in fixes if f["replacement"].startswith("[figure removed")
        ),
        "currency_fixed": sum(1 for f in fixes if f["reason"].startswith("currency")),
        "claim_removed": sum(1 for p in receipt["provenance"] if p["status"] == "removed"),
        "material_omission": int("MATERIAL_OMISSION" in receipt["risk_flags"]),
        "turn_failed": int(presented.get("outcome") == "rejected" and "message" in presented),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("out", type=Path)
    parser.add_argument("--temperature", type=float, default=float("0.7"))
    args = parser.parse_args(argv)
    out: Path = args.out
    out.mkdir(parents=True, exist_ok=True)

    policy = load_policy(ROOT / "config" / "policy.yaml")
    model = load_model_config(ROOT / "config" / "model.yaml")
    sampled = model.local.model_copy(update={"temperature": args.temperature})

    rows: list[dict[str, Any]] = []
    for turn_index, (name, request, samples) in enumerate(TURNS):
        (out / name).mkdir(exist_ok=True)
        for i in range(samples):
            seed = 1000 * (turn_index + 1) + i
            with tempfile.TemporaryDirectory() as tmp:
                db = Path(tmp) / "ledger.sqlite"
                shutil.copyfile(ROOT / "data" / "ledger.sqlite", db)
                sealer = Sealer(bytes(32))
                executor = Executor(db, sealer, policy)
                reads = make_readonly_sessionmaker(db)
                verifier = Verifier(sealer, policy)
                client = Capturing(LocalClient(sampled, seed=seed))
                orchestrator = Orchestrator(
                    client,
                    CopilotService(reads, verifier, executor),
                    verifier,
                    reads,
                    policy,
                    model.max_intent_retries,
                )
                started = time.monotonic()
                record = orchestrator.run(request, f"probe_{name}_{i}"[:40])
                executor.dispose()
            result = record.result
            assert result is not None
            counts = interventions(result.receipt, result.presented)
            sample = {
                "turn": name,
                "sample": i,
                "seed": seed,
                "temperature": args.temperature,
                "seconds": round(time.monotonic() - started),
                "counts": counts,
                "steps": record.steps,
                "exchanges": client.exchanges,
                "presented": result.presented,
                "receipt": result.receipt,
            }
            path = out / name / f"sample_{i:02d}.json"
            path.write_text(json.dumps(sample, indent=1, ensure_ascii=False) + "\n")
            rows.append({"turn": name, "sample": i, "seed": seed, **counts})
            print(json.dumps({"turn": name, "sample": i, **counts}), flush=True)

    summary = summarise(rows, args.temperature, model.local.model)
    (out / "summary.json").write_text(json.dumps(summary, indent=1) + "\n")
    print(json.dumps(summary["overall"], indent=1))
    return 0


def summarise(rows: list[dict[str, Any]], temperature: float, model: str) -> dict[str, Any]:
    keys = [k for k in rows[0] if k not in ("turn", "sample", "seed")]

    def block(selected: list[dict[str, Any]]) -> dict[str, Any]:
        totals = Counter({k: sum(r[k] for r in selected) for k in keys})
        touched = sum(
            1
            for r in selected
            if r["figure_corrected"]
            + r["text_figure_corrected"]
            + r["text_figure_masked"]
            + r["figure_not_recomputable"]
            > 0
        )
        return {
            "samples": len(selected),
            "samples_with_a_figure_corrected_masked_or_removed": touched,
            "totals": dict(totals),
        }

    turns = sorted({r["turn"] for r in rows})
    return {
        "model": model,
        "temperature": temperature,
        "overall": block(rows),
        "by_turn": {t: block([r for r in rows if r["turn"] == t]) for t in turns},
    }


if __name__ == "__main__":
    sys.exit(main())
