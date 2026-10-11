"""Record one turn against the local model, for replay without a model server.

    uv run python scripts/record_turn.py s01_cash_quarter \
        "How is our cash looking for the next quarter?"

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
from ledgerguard.verifier.pipeline import Verifier
from ledgerguard.verifier.seal import Sealer

ROOT = Path(__file__).resolve().parent.parent


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Record one turn as a cassette.")
    parser.add_argument("name", help="cassette name, e.g. s01_cash_quarter")
    parser.add_argument("request", help="what the owner asks")
    args = parser.parse_args(argv)

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
            ROOT / model.replay.cassette_dir / f"{args.name}.json",
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
        record = orchestrator.run(
            args.request, f"rec_{args.name}", lambda s: print(json.dumps(s, ensure_ascii=False))
        )
        executor.dispose()
    result = record.result
    assert result is not None
    print(json.dumps(result.presented, indent=1, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
