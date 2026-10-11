"""Turn one robustness-probe sample into a recording the UI can replay.

    uv run python scripts/sample_to_cassette.py \\
        evidence/robustness/s09_pay_all_suppliers/sample_01.json OUT_DIR

The sample's stored model responses are played back, in order, through the
real orchestrator and verifier on a fresh copy of the seeded ledger, and
written as a cassette in OUT_DIR. Point a second server's replay directory at
OUT_DIR to see that exact turn in the UI. The product's own recordings are not
touched, because a sampled response would collide with them: the replay key
does not include the temperature.
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
from ledgerguard.proposer.client import LLMClient, LLMResponse, RecordingClient
from ledgerguard.proposer.orchestrator import Orchestrator
from ledgerguard.service import CopilotService
from ledgerguard.verifier.pipeline import Verifier
from ledgerguard.verifier.seal import Sealer

ROOT = Path(__file__).resolve().parent.parent


class Stored(LLMClient):
    def __init__(self, exchanges: list[dict[str, Any]]) -> None:
        self._exchanges = list(exchanges)

    def complete(self, messages: list[dict[str, str]], schema: dict[str, Any]) -> LLMResponse:
        exchange = self._exchanges.pop(0)
        if exchange["messages"] != messages:
            raise AssertionError("the turn did not ask what the sample recorded")
        return LLMResponse(exchange["response"], "stored", exchange["elapsed_ms"])


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("sample", type=Path)
    parser.add_argument("out", type=Path)
    args = parser.parse_args(argv)
    sample = json.loads(args.sample.read_text())
    request = next(e["messages"][1]["content"] for e in sample["exchanges"])
    policy = load_policy(ROOT / "config" / "policy.yaml")
    model = load_model_config(ROOT / "config" / "model.yaml")
    name = f"{sample['turn']}_sample_{sample['sample']:02d}"
    with tempfile.TemporaryDirectory() as tmp:
        db = Path(tmp) / "ledger.sqlite"
        shutil.copyfile(ROOT / "data" / "ledger.sqlite", db)
        sealer = Sealer(bytes(32))
        executor = Executor(db, sealer, policy)
        reads = make_readonly_sessionmaker(db)
        verifier = Verifier(sealer, policy)
        client = RecordingClient(
            Stored(sample["exchanges"]), args.out / f"{name}.json", model.local.model
        )
        orchestrator = Orchestrator(
            client,
            CopilotService(reads, verifier, executor),
            verifier,
            reads,
            policy,
            model.max_intent_retries,
        )
        record = orchestrator.run(request, "sample_replay")
        executor.dispose()
    assert record.result is not None
    print(
        json.dumps(
            {
                "cassette": str(args.out / f"{name}.json"),
                "request": request,
                "flags": record.result.receipt["risk_flags"],
            }
        )
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
