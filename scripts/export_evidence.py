"""Run the whole demo offline and write the execution evidence.

    uv run python scripts/export_evidence.py

The demo runs from the recordings, with no model server and no network, on a
fresh copy of the seeded ledger, in the order a judge should follow: the
read-only scenarios, the adversarial fixtures and the requests that need
approval. Then an operator works through the Approval Inbox: approving the
receipt and the financing acceptance, declining the suspected duplicate
payment and the slow-burn payment. The audit trail comes last. The T3 cool-off
is passed by moving a clock, not by waiting.

Every request is made before any approval, because approving changes the
ledger and a later request would then no longer match its recording.

Output, all of it regenerated on every run:

    evidence/scenarios/<id>.json   each turn's steps, verified result and receipt
    evidence/receipts/<id>.json    every Trust Receipt
    evidence/approvals.json        what was approved or declined, and the changes
    evidence/audit_chain.json      every audit event, and the chain check
    evidence/README.md             an index of the above
"""

from __future__ import annotations

import json
import shutil
import sys
import tempfile
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from ledgerguard.audit.store import event_dict, load_events, verify_stored
from ledgerguard.config import load_model_config, load_policy
from ledgerguard.db.engine import make_readonly_sessionmaker
from ledgerguard.domain.enums import Actor
from ledgerguard.executor.executor import Executor
from ledgerguard.proposer.client import ReplayClient
from ledgerguard.proposer.orchestrator import Orchestrator
from ledgerguard.service import CopilotService
from ledgerguard.untrusted import Attachment
from ledgerguard.verifier.pipeline import Verifier
from ledgerguard.verifier.seal import Sealer

ROOT = Path(__file__).resolve().parent.parent
EVIDENCE = ROOT / "evidence"

# Which pending action each approval scenario leaves, and what the operator does.
DECISIONS = {
    "s06_accept_factoring": ("approve", None),
    "s07_record_brennan_payment": ("approve", None),
    "s08_pay_invoice_0377": ("decline", "Suspected duplicate of inv_0641, already paid."),
    "a6_slow_burn": ("decline", "Arrived with a request to change bank details. Checked by phone."),
}


class Clock:
    def __init__(self) -> None:
        self.now = datetime.now(UTC).replace(microsecond=0)

    def __call__(self) -> datetime:
        return self.now


def main() -> int:
    catalogue = json.loads((ROOT / "fixtures" / "scenarios.json").read_text())
    entries = {e["id"]: e for e in [*catalogue["scenarios"], *catalogue["adversarial"]]}
    reads = [s["id"] for s in catalogue["scenarios"] if s["phase"] == "read"]
    reads.remove("s12_audit_trail")
    fixtures = [a["id"] for a in catalogue["adversarial"] if "turns" in a]
    approvals = [s["id"] for s in catalogue["scenarios"] if s["phase"] == "approval"]
    order = reads + fixtures + approvals + ["s12_audit_trail"]

    for sub in ("scenarios", "receipts"):
        shutil.rmtree(EVIDENCE / sub, ignore_errors=True)
        (EVIDENCE / sub).mkdir(parents=True)

    policy = load_policy(ROOT / "config" / "policy.yaml")
    model = load_model_config(ROOT / "config" / "model.yaml")
    clock = Clock()
    index: list[dict[str, Any]] = []
    decisions: list[dict[str, Any]] = []

    with tempfile.TemporaryDirectory() as tmp:
        db = Path(tmp) / "ledger.sqlite"
        shutil.copyfile(ROOT / "data" / "ledger.sqlite", db)
        sealer = Sealer(bytes(32))
        executor = Executor(db, sealer, policy, clock)
        reads_session = make_readonly_sessionmaker(db)
        verifier = Verifier(sealer, policy, clock=clock)
        service = CopilotService(reads_session, verifier, executor, clock)
        orchestrator = Orchestrator(
            ReplayClient(ROOT / model.replay.cassette_dir),
            service,
            verifier,
            reads_session,
            policy,
            model.max_intent_retries,
        )
        executor.record(Actor.SYSTEM, "system.started", {"llm_client": "replay", "run": "evidence"})

        waiting: list[tuple[str, str, dict[str, Any]]] = []
        for entry_id in order:
            if entry_id == "s12_audit_trail":
                waiting = work_the_inbox(waiting, service, clock, policy, decisions)
            entry = entries[entry_id]
            turns = []
            for n, turn in enumerate(entry["turns"], start=1):
                clock.now += timedelta(seconds=5)
                docs = [
                    Attachment(Path(p).name, (ROOT / p).read_text())
                    for p in turn.get("attachments", [])
                ]
                record = orchestrator.run(turn["request"], f"ev_{entry_id}"[:40], None, docs)
                result = record.result
                assert result is not None
                turns.append(
                    {
                        "turn": n,
                        "request": turn["request"],
                        "attachments": [d.name for d in docs],
                        "steps": record.steps,
                        "presented": result.presented,
                        "receipt": result.receipt,
                    }
                )
                write(
                    EVIDENCE / "receipts" / f"{result.receipt['receipt_id']}.json", result.receipt
                )
                if result.status == "pending_approval":
                    waiting.append((entry_id, result.intent_id, result.receipt))
            write(EVIDENCE / "scenarios" / f"{entry_id}.json", {"id": entry_id, "turns": turns})
            last = turns[-1]
            index.append(
                {
                    "id": entry_id,
                    "turns": len(turns),
                    "outcome": last["presented"]["outcome"],
                    "tier": last["presented"].get("tier"),
                    "flags": last["receipt"]["risk_flags"],
                    "contained": any(s["step"] == "neutralised" for t in turns for s in t["steps"]),
                }
            )

        with reads_session() as s:
            report = verify_stored(s)
            events = [event_dict(e) for e in load_events(s)]
        executor.dispose()

    write(EVIDENCE / "approvals.json", decisions)
    write(EVIDENCE / "audit_chain.json", {"verification": report.as_dict(), "events": events})
    (EVIDENCE / "README.md").write_text(readme(index, decisions, report.as_dict()))
    print(json.dumps({"scenarios": len(index), "chain": report.as_dict()}, indent=1))
    return 0


def work_the_inbox(
    waiting: list[tuple[str, str, dict[str, Any]]],
    service: CopilotService,
    clock: Clock,
    policy: Any,
    decisions: list[dict[str, Any]],
) -> list[tuple[str, str, dict[str, Any]]]:
    """The operator's decisions, in the order the requests arrived."""
    clock.now += timedelta(seconds=policy.t3.cool_off_seconds + 1)
    for entry_id, intent_id, receipt in waiting:
        action, reason = DECISIONS[entry_id]
        clock.now += timedelta(seconds=5)
        if action == "approve":
            phrase = receipt["approval"]["confirmation_phrase"]
            done = service.approve(intent_id, Actor.OPERATOR, phrase)
            decisions.append(
                {
                    "scenario": entry_id,
                    "intent_id": intent_id,
                    "decision": "approved",
                    "tier": receipt["tier"],
                    "confirmation": phrase,
                    "changes": done.changes,
                    "audit_seq": done.audit_seq,
                }
            )
        else:
            seq = service.decline(intent_id, Actor.OPERATOR, str(reason))
            decisions.append(
                {
                    "scenario": entry_id,
                    "intent_id": intent_id,
                    "decision": "declined",
                    "reason": reason,
                    "audit_seq": seq,
                }
            )
    return []


def write(path: Path, data: Any) -> None:
    path.write_text(json.dumps(data, indent=1, ensure_ascii=False, default=str) + "\n")


def readme(
    index: list[dict[str, Any]], decisions: list[dict[str, Any]], chain: dict[str, Any]
) -> str:
    lines = [
        "# Execution evidence",
        "",
        "Generated by `scripts/export_evidence.py` from the recordings in "
        "`fixtures/llm_cassettes`, with no model server and no network, on a fresh copy of the "
        "seeded sandbox ledger. "
        "Run it again to reproduce every file here.",
        "",
        f"The audit chain is {'intact' if chain['intact'] else 'broken'}: "
        f"{chain['events']} events, head hash `{chain['head_hash']}`.",
        "",
        "| Scenario | Turns | Outcome | Tier | Contained | Risk flags |",
        "|---|---|---|---|---|---|",
    ]
    for row in index:
        lines.append(
            f"| [{row['id']}](scenarios/{row['id']}.json) | {row['turns']} | {row['outcome']} | "
            f"{row['tier'] or ''} | {'yes' if row['contained'] else ''} | "
            f"{', '.join(row['flags'])} |"
        )
    lines += ["", "Operator decisions, in [approvals.json](approvals.json):", ""]
    for d in decisions:
        detail = (
            f"{len(d['changes'])} ledger change{'' if len(d['changes']) == 1 else 's'}, "
            f"audit event {d['audit_seq']}"
            if d["decision"] == "approved"
            else d["reason"]
        )
        lines.append(f"* {d['scenario']}: {d['decision']}. {detail}")
    lines += [
        "",
        "`robustness/` holds a separate experiment, described in its own summary. It is not part "
        "of the demo run.",
        "",
    ]
    return "\n".join(lines)


if __name__ == "__main__":
    sys.exit(main())
