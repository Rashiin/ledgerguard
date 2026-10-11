"""Run the same requests against two different local models and compare what the verifier does.

    uv run python scripts/model_swap_demo.py qwen2.5-7b-instruct mistral-7b-instruct-v0.3
    uv run python scripts/model_swap_demo.py --replay

The claim under test is about the verifier, not the models: a weaker or
different model may answer worse, but it cannot get a blocked payment through.
Each model gets the same request, the same prompt, the same grammar and the
same retry budget, on a fresh copy of the seeded ledger. Nothing is adjusted
for either model. If a model cannot produce a valid action at all, that is a
result and it is reported, not worked around.

Live runs record every response under evidence/model_swap/<model>/. With
--replay the recorded responses are used, so the comparison runs offline.
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
import tempfile
from pathlib import Path
from typing import Any

from ledgerguard.config import load_model_config, load_policy
from ledgerguard.db.engine import make_readonly_sessionmaker
from ledgerguard.executor.executor import Executor
from ledgerguard.proposer.client import LLMClient, LocalClient, RecordingClient, ReplayClient
from ledgerguard.proposer.orchestrator import Orchestrator
from ledgerguard.service import CopilotService
from ledgerguard.verifier.pipeline import Verifier
from ledgerguard.verifier.seal import Sealer

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "evidence" / "model_swap"
# Scenario 9 leaves the model to pick the invoices from a date. The a2 request names a
# single invoice, so both models face the same action and only the verdict could differ.
SCENARIOS = ("s09_pay_all_suppliers", "a2_supplier_name")


def request(scenario: str) -> str:
    catalogue = json.loads((ROOT / "fixtures" / "scenarios.json").read_text())
    entries = [*catalogue["scenarios"], *catalogue["adversarial"]]
    entry = next(s for s in entries if s["id"] == scenario)
    return str(entry["turns"][0]["request"])


def run(model_name: str, scenario: str, replay: bool) -> dict[str, Any]:
    policy = load_policy(ROOT / "config" / "policy.yaml")
    model = load_model_config(ROOT / "config" / "model.yaml")
    folder = OUT / model_name
    client: LLMClient
    if replay:
        client = ReplayClient(folder)
    else:
        folder.mkdir(parents=True, exist_ok=True)
        local = model.local.model_copy(update={"model": model_name})
        client = RecordingClient(
            LocalClient(local, seed=42), folder / f"{scenario}.json", model_name
        )
    with tempfile.TemporaryDirectory() as tmp:
        db = Path(tmp) / "ledger.sqlite"
        shutil.copyfile(ROOT / "data" / "ledger.sqlite", db)
        sealer = Sealer(bytes(32))
        executor = Executor(db, sealer, policy)
        reads = make_readonly_sessionmaker(db)
        verifier = Verifier(sealer, policy)
        orchestrator = Orchestrator(
            client,
            CopilotService(reads, verifier, executor),
            verifier,
            reads,
            policy,
            model.max_intent_retries,
        )
        record = orchestrator.run(request(scenario), f"swap_{model_name}"[:40])
        executor.dispose()
    result = record.result
    assert result is not None
    models = [s for s in record.steps if s["step"] == "model"]
    return {
        "model": model_name,
        "scenario": scenario,
        "request": request(scenario),
        "outcome": result.outcome,
        "status": result.status,
        "tier": result.presented.get("tier"),
        "blocked_by": [b["rule"] for b in result.presented.get("blocked_by", [])],
        "payment_total": (result.receipt.get("payload_figures") or {}).get("payment_total"),
        "model_calls": len(models),
        "rejected_attempts": [s["errors"] for s in record.steps if s["step"] == "rejected"],
        "dry_run_blocks": next((s["blocks"] for s in record.steps if s["step"] == "dry_run"), None),
        "flags": result.receipt["risk_flags"],
        "message": result.presented.get("message"),
        "steps": record.steps,
        "presented": result.presented,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("models", nargs="*")
    parser.add_argument("--replay", action="store_true")
    args = parser.parse_args(argv)
    names = args.models or sorted(p.name for p in OUT.iterdir() if p.is_dir())
    results = [run(name, scenario, args.replay) for scenario in SCENARIOS for name in names]
    for r in results:
        print(
            f"{r['scenario']:24s} {r['model']:28s} outcome={r['outcome']:9s} "
            f"blocked_by={r['blocked_by']} "
            f"total={r['payment_total']} calls={r['model_calls']} "
            f"rejected={len(r['rejected_attempts'])}"
        )
    if not args.replay:
        (OUT / "summary.json").write_text(json.dumps(results, indent=1, ensure_ascii=False) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
