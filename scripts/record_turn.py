"""Record turns against the local model, for replay without a model server.

    uv run python scripts/record_turn.py --scenario s01_cash_quarter
    uv run python scripts/record_turn.py my_name "Any request"

``--scenario`` plays an entry of fixtures/scenarios.json: every turn in order,
in one conversation, with its attachments, into one cassette named after it.

The turn runs on a fresh copy of the seeded ledger, so the prompt is exactly the
one a replay will see. The model's responses are written to
fixtures/llm_cassettes/<name>.json unedited. Run seed.py first.
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
import tempfile
from pathlib import Path

from ledgerguard.config import load_model_config, load_policy
from ledgerguard.db.engine import make_readonly_sessionmaker
from ledgerguard.executor.executor import Executor
from ledgerguard.proposer.client import LocalClient, RecordingClient
from ledgerguard.proposer.orchestrator import Orchestrator
from ledgerguard.service import CopilotService
from ledgerguard.untrusted import Attachment
from ledgerguard.verifier.pipeline import Verifier
from ledgerguard.verifier.seal import Sealer

ROOT = Path(__file__).resolve().parent.parent


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Record turns as a cassette.")
    parser.add_argument("name", nargs="?", help="cassette name, e.g. s01_cash_quarter")
    parser.add_argument("request", nargs="?", help="what the owner asks")
    parser.add_argument("--scenario", help="an id from fixtures/scenarios.json")
    args = parser.parse_args(argv)
    if args.scenario:
        name, turns = args.scenario, catalogue_turns(args.scenario)
    elif args.name and args.request:
        name, turns = args.name, [(args.request, [])]
    else:
        parser.error("give --scenario, or a name and a request")

    policy = load_policy(ROOT / "config" / "policy.yaml")
    model = load_model_config(ROOT / "config" / "model.yaml")
    with tempfile.TemporaryDirectory() as tmp:
        db = Path(tmp) / "ledger.sqlite"
        shutil.copyfile(ROOT / "data" / "ledger.sqlite", db)
        sealer = Sealer(bytes(32))
        executor = Executor(db, sealer, policy)
        reads = make_readonly_sessionmaker(db)
        verifier = Verifier(sealer, policy)
        client = RecordingClient(
            LocalClient(model.local, seed=42),
            ROOT / model.replay.cassette_dir / f"{name}.json",
            model.local.model,
        )
        orchestrator = Orchestrator(
            client,
            CopilotService(reads, verifier, executor),
            verifier,
            reads,
            policy,
            model.max_intent_retries,
        )
        for request, attachments in turns:
            print(json.dumps({"request": request}, ensure_ascii=False))
            record = orchestrator.run(
                request,
                f"rec_{name}"[:40],
                lambda s: print(json.dumps(s, ensure_ascii=False)),
                attachments,
            )
            result = record.result
            assert result is not None
            print(json.dumps(result.presented, indent=1, ensure_ascii=False))
        executor.dispose()
    return 0


def catalogue_turns(scenario_id: str) -> list[tuple[str, list[Attachment]]]:
    catalogue = json.loads((ROOT / "fixtures" / "scenarios.json").read_text())
    entry = next(
        e for e in [*catalogue["scenarios"], *catalogue["adversarial"]] if e["id"] == scenario_id
    )
    return [
        (
            t["request"],
            [Attachment(Path(p).name, (ROOT / p).read_text()) for p in t.get("attachments", [])],
        )
        for t in entry["turns"]
    ]


if __name__ == "__main__":
    sys.exit(main())
