"""One turn, from the owner's words to a verified result.

1. Plan. The allowed kinds and tools come from the request alone.
2. Tools. They run on the read-only snapshot and return facts. Anything
   instruction-like that was stripped from ledger text is written to the audit
   log.
3. Draft. The model answers under the turn's schema. A draft that fails the
   strict parser is retried at most twice, with the parser's errors sent back
   unchanged. After that the turn fails visibly. Output is never repaired.
4. Dry run. For kinds that change records or move money, the rules run on the
   draft. If anything blocks, the model sees the block as a fact it can cite and
   writes its final answer once more.
5. Verify. The final output goes to the verifier with the turn context, which
   records the tools that actually ran. The model never sets that record.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy.orm import Session, sessionmaker

from ledgerguard.config import Policy
from ledgerguard.domain.enums import ActionKind, Actor
from ledgerguard.facts import CHECK_ACTION, block_fact
from ledgerguard.proposer import prompt
from ledgerguard.proposer.allowlist import TurnPlan, plan_turn
from ledgerguard.proposer.client import LLMClient
from ledgerguard.proposer.schema import intent_schema
from ledgerguard.proposer.tools import ToolResult, run_tools
from ledgerguard.rules import Outcome
from ledgerguard.service import CopilotService, TurnResult
from ledgerguard.verifier.intent import parse_intent
from ledgerguard.verifier.pipeline import TurnContext, Verifier
from ledgerguard.verifier.snapshot import load_snapshot

COULD_NOT = "The agent could not produce a valid action, so nothing was done."
# Kinds whose draft is dry-run against the rules before the final answer.
DRY_RUN_KINDS = frozenset(
    {
        ActionKind.DRAFT_INVOICE,
        ActionKind.RECORD_PAYMENT,
        ActionKind.MARK_DISPUTED,
        ActionKind.INITIATE_PAYMENT,
        ActionKind.ACCEPT_FINANCING_OFFER,
    }
)

Step = dict[str, Any]


@dataclass
class TurnRecord:
    request: str
    plan: TurnPlan
    steps: list[Step] = field(default_factory=list)
    result: TurnResult | None = None


class Orchestrator:
    def __init__(
        self,
        client: LLMClient,
        service: CopilotService,
        verifier: Verifier,
        read_sessions: sessionmaker[Session],
        policy: Policy,
        max_retries: int,
    ) -> None:
        self._client = client
        self._service = service
        self._verifier = verifier
        self._reads = read_sessions
        self._policy = policy
        self._max_retries = max_retries

    def run(
        self, request: str, conversation_id: str, on_step: Callable[[Step], None] | None = None
    ) -> TurnRecord:
        plan = plan_turn(request)
        record = TurnRecord(request, plan)

        def step(kind: str, **detail: Any) -> None:
            event = {"step": kind, **detail}
            record.steps.append(event)
            if on_step is not None:
                on_step(event)

        step(
            "plan",
            kinds=sorted(k.value for k in plan.kinds),
            tools=list(plan.tools),
            routes=list(plan.matched),
        )
        with self._reads() as session:
            snapshot = load_snapshot(session)
        currency = snapshot.business.currency
        threshold = self._policy.rules.concentration_warning_share
        results = run_tools(snapshot, list(plan.tools), threshold)
        self._log_stripped(results, conversation_id, step)
        step("tools", ran=list(plan.tools), facts=sum(len(r.facts) for r in results))

        schema = intent_schema(plan.kinds)
        messages = prompt.messages(request, plan.kinds, currency, results)
        raw, ok = self._ask(messages, schema, step, phase="draft")
        tools_called = list(plan.tools)

        draft = parse_intent(raw).intent if ok else None
        if draft is not None and draft.kind in DRY_RUN_KINDS:
            tools_called.append(CHECK_ACTION)
            errors, rules = self._verifier.dry_run(
                draft.kind.value, draft.payload.model_dump(mode="json"), snapshot
            )
            blocks = [
                block_fact(r.rule, r.reason, r.rows) for r in rules if r.result is Outcome.BLOCK
            ]
            step("dry_run", payload_errors=errors, blocks=[f.id for f in blocks])
            if blocks:
                follow_up = [
                    *messages,
                    {"role": "assistant", "content": raw},
                    {"role": "user", "content": prompt.dry_run_message(blocks)},
                ]
                # The final answer is about the same kind of action. The model may
                # shrink it, but it cannot turn a blocked payment into a chat answer.
                final_schema = intent_schema([draft.kind])
                raw, ok = self._ask(follow_up, final_schema, step, phase="final")

        result = self._service.submit(raw, conversation_id, TurnContext(tuple(tools_called)))
        if not ok:
            result.presented["message"] = COULD_NOT
        step(
            "verified",
            outcome=result.outcome,
            status=result.status,
            receipt_id=result.receipt["receipt_id"],
            flags=result.receipt["risk_flags"],
        )
        record.result = result
        return record

    def _ask(
        self,
        messages: list[dict[str, str]],
        schema: dict[str, Any],
        step: Callable[..., None],
        phase: str,
    ) -> tuple[str, bool]:
        """Ask the model, retrying a malformed answer at most ``max_retries`` times."""
        history = list(messages)
        raw = ""
        for attempt in range(self._max_retries + 1):
            reply = self._client.complete(history, schema)
            raw = reply.content
            parsed = parse_intent(raw)
            step(
                "model",
                phase=phase,
                attempt=attempt + 1,
                elapsed_ms=reply.elapsed_ms,
                valid=parsed.intent is not None,
            )
            if parsed.intent is not None:
                return raw, True
            errors = "\n".join(f"{e.location}: {e.message}" for e in parsed.errors)
            step("rejected", phase=phase, attempt=attempt + 1, errors=errors)
            history += [
                {"role": "assistant", "content": raw},
                {"role": "user", "content": prompt.retry_message(errors)},
            ]
        return raw, False

    def _log_stripped(
        self, results: list[ToolResult], conversation_id: str, step: Callable[..., None]
    ) -> None:
        stripped = [
            {"fact_id": f.id, "removed": list(f.stripped)}
            for r in results
            for f in r.facts
            if f.stripped
        ]
        if stripped:
            self._service.note(
                Actor.SYSTEM,
                "untrusted.neutralised",
                {"conversation_id": conversation_id, "spans": stripped},
            )
            step("neutralised", spans=stripped)


def summary(record: TurnRecord) -> str:
    """A compact JSON line for logs and scripts."""
    result = record.result
    return json.dumps(
        {
            "request": record.request,
            "outcome": None if result is None else result.outcome,
            "flags": [] if result is None else result.receipt["risk_flags"],
            "steps": [s["step"] for s in record.steps],
        }
    )
