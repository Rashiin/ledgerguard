"""The verifier plane: six deterministic stages from raw model output to a sealed action.

    1 schema · 2 recomputation · 3 business rules · 4 provenance · 5 policy · 6 tier

The result is a ``Verification``:

* **outcome:** verified, blocked or rejected;
* **receipt:** the Trust Receipt;
* **action:** for a verified outcome only, the sealed ``VerifiedAction`` the
  Executor will accept;
* **presented:** the version of the model's answer the user is allowed to
  see. Figures are recomputed, unsupported claims are visibly removed,
  unverified numbers and guarantee language are masked, and disclosures are
  attached.

The verifier reads the parsed intent and the ledger snapshot. It never reads
the prompt or the conversation.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

from ledgerguard.audit.chain import timestamp
from ledgerguard.config import Policy
from ledgerguard.domain.enums import Tier
from ledgerguard.rules import NEVER_AUTO_PASS, Outcome, RuleContext, RuleResult, evaluate
from ledgerguard.verifier import intent as I
from ledgerguard.verifier.policy import (
    ADVISORY_KINDS,
    FINANCING_DISCLOSURE,
    PolicyFinding,
    check_financing,
    mask_guarantees,
)
from ledgerguard.verifier.provenance import REMOVED, ProvenanceFinding, check_claim
from ledgerguard.verifier.receipt import Approval, TrustReceipt
from ledgerguard.verifier.recompute import (
    Recomputation,
    Resolution,
    recompute_claim,
    resolve_payload,
)
from ledgerguard.verifier.seal import Sealer, VerifiedAction
from ledgerguard.verifier.snapshot import LedgerSnapshot, table_of
from ledgerguard.verifier.text import TextFix, Verified, check_text, verified_pool
from ledgerguard.verifier.tiers import assign_tier

VERIFIER_VERSION = "1.0.0"
MALFORMED = "The agent produced a malformed action, so nothing was done."
APPROVERS = "operator"


def t3_phrase(intent_id: str) -> str:
    return f"CONFIRM {intent_id}"


@dataclass
class Verification:
    intent_id: str
    outcome: str
    tier: Tier | None
    receipt: TrustReceipt
    action: VerifiedAction | None
    presented: dict[str, Any]
    intent: I.ActionIntent | None
    raw_output: str
    rule_results: list[RuleResult] = field(default_factory=list)


def _new_id() -> str:
    return uuid.uuid4().hex[:16]


class Verifier:
    def __init__(
        self,
        sealer: Sealer,
        policy: Policy,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
        new_id: Callable[[], str] = _new_id,
    ) -> None:
        self._sealer = sealer
        self._policy = policy
        self._clock = clock
        self._new_id = new_id

    def verify(self, raw: str | bytes, snapshot: LedgerSnapshot) -> Verification:
        token = self._new_id()
        intent_id, receipt_id = f"int_{token}", f"rcp_{token}"
        now = self._clock()
        raw_text = raw.decode("utf-8", "replace") if isinstance(raw, bytes) else raw

        # ---- stage 1: schema
        parsed = I.parse_intent(raw)
        flags: set[str] = set()
        if parsed.ignored_tier is not None:
            flags.add("MODEL_TIER_IGNORED")
        if parsed.intent is None:
            receipt = self._receipt(
                receipt_id,
                intent_id,
                "rejected",
                None,
                None,
                now,
                flags=flags | {"MALFORMED_ACTION"},
                schema_errors=[
                    {"location": e.location, "message": e.message} for e in parsed.errors
                ],
            )
            return Verification(
                intent_id,
                "rejected",
                None,
                receipt,
                None,
                {
                    "intent_id": intent_id,
                    "outcome": "rejected",
                    "message": MALFORMED,
                    "errors": receipt.schema_errors,
                    "receipt_id": receipt_id,
                },
                None,
                raw_text,
            )
        intent = parsed.intent
        reads: set[str] = set()

        # ---- stage 2: recomputation (claims, then what the payload would move)
        recomps = [recompute_claim(snapshot, i, c, reads) for i, c in enumerate(intent.claims)]
        resolution = resolve_payload(snapshot, intent, reads)

        # ---- stage 4 runs before the rules need citations: provenance per claim
        prov = [check_claim(snapshot, i, c, recomps[i], reads) for i, c in enumerate(intent.claims)]
        cited = frozenset(
            r for c in intent.claims for r in c.source_row_ids if snapshot.row(r) is not None
        )

        if resolution.errors:
            receipt = self._receipt(
                receipt_id,
                intent_id,
                "rejected",
                intent,
                None,
                now,
                flags=flags | {"INVALID_PAYLOAD_REFERENCE"},
                reads=reads,
                recomps=recomps,
                prov=prov,
                schema_errors=[{"location": "payload", "message": e} for e in resolution.errors],
            )
            return Verification(
                intent_id,
                "rejected",
                None,
                receipt,
                None,
                {
                    "intent_id": intent_id,
                    "kind": intent.kind.value,
                    "outcome": "rejected",
                    "message": "The proposed action refers to something the ledger does not "
                    "support, so nothing was done.",
                    "errors": resolution.errors,
                    "receipt_id": receipt_id,
                },
                intent,
                raw_text,
            )

        # ---- stage 3: business rules
        rules = evaluate(RuleContext(intent, resolution, snapshot, self._policy, cited))
        for r in rules:
            reads.update(x for x in r.rows if table_of(x))
            if r.result is not Outcome.PASS:
                flags.update(r.flags)

        # ---- stage 5: policy, and the text the user may see
        presented_claims, text_fixes, guarantee_hits = self._present(
            intent, recomps, prov, resolution
        )
        rationale, fixes = check_text(
            "rationale", intent.rationale, self._pool(recomps, resolution)
        )
        text_fixes += fixes
        rationale, hits = mask_guarantees(rationale)
        guarantee_hits += hits
        policy_findings, disclosure = check_financing(intent, recomps)
        if guarantee_hits:
            policy_findings.append(
                PolicyFinding(
                    "no_guaranteed_outcomes",
                    "corrected",
                    f"masked {len(guarantee_hits)} guarantee phrase(s): "
                    + "; ".join(f'"{h}"' for h in guarantee_hits)
                    + (" in advisory output" if intent.kind in ADVISORY_KINDS else ""),
                )
            )
            flags.add("GUARANTEED_OUTCOME_LANGUAGE")
        if any(rc.status == "mismatch" for rc in recomps):
            flags.add("FIGURE_MISMATCH")
        if any(p.status == "figure_removed" for p in prov):
            flags.add("UNVERIFIED_FIGURE")
        if any(p.status == "removed" for p in prov):
            flags.add("UNSUPPORTED_CLAIM_REMOVED")
        if any(f.reason == "no verified figure matches" for f in text_fixes):
            flags.add("TEXT_FIGURE_MASKED")

        # ---- stage 6: tier
        needs_human = bool(flags & NEVER_AUTO_PASS)
        tier = assign_tier(
            intent.kind, resolution.amount, self._policy.amount_ceilings, needs_human
        )
        blocking = tuple(r.rule for r in rules if r.result is Outcome.BLOCK)
        violations = [p for p in policy_findings if p.result == "violation"]
        outcome = "blocked" if blocking or violations else "verified"
        if violations:
            flags.add("POLICY_VIOLATION")

        receipt = self._receipt(
            receipt_id,
            intent_id,
            outcome,
            intent,
            tier,
            now,
            flags=flags,
            reads=reads,
            recomps=recomps,
            prov=prov,
            rules=rules,
            policy=policy_findings,
            text_fixes=text_fixes,
            resolution=resolution,
            disclosures=[FINANCING_DISCLOSURE] if disclosure else [],
        )
        action = None
        if outcome == "verified":
            action = self._sealer.seal(
                VerifiedAction(
                    intent_id=intent_id,
                    kind=intent.kind,
                    tier=tier,
                    payload={
                        **intent.payload.model_dump(mode="json"),
                        "resolved": _resolved(resolution),
                    },
                    receipt_id=receipt_id,
                    verifier_version=VERIFIER_VERSION,
                    verified_at=now,
                )
            )
        presented = {
            "intent_id": intent_id,
            "kind": intent.kind.value,
            "outcome": outcome,
            "tier": tier.value,
            "claims": presented_claims,
            "rationale": rationale,
            "blocked_by": [
                {"rule": r.rule, "reason": r.reason} for r in rules if r.result is Outcome.BLOCK
            ]
            + [{"rule": "Policy", "reason": p.detail} for p in violations],
            "warnings": [
                {"rule": r.rule, "reason": r.reason} for r in rules if r.result is Outcome.WARN
            ],
            "disclosures": receipt.disclosures,
            "approval": receipt.approval.model_dump(),
            "receipt_id": receipt_id,
        }
        return Verification(
            intent_id, outcome, tier, receipt, action, presented, intent, raw_text, rules
        )

    def recheck(self, kind: str, payload: dict[str, Any], snapshot: LedgerSnapshot) -> list[str]:
        """Re-run payload resolution and the rules against the ledger as it is now.

        Used at approval time: an action verified earlier may no longer be safe,
        for example once another approved payment has drained the payroll cover.
        Returns the reasons it must now be refused (empty if it is still fine).
        """
        payload = {k: v for k, v in payload.items() if k != "resolved"}
        parsed = I.parse_intent(
            json.dumps(
                {
                    "kind": kind,
                    "payload": payload,
                    "claims": [],
                    "rationale": "approval-time re-check",
                }
            )
        )
        if parsed.intent is None:
            return ["stored action no longer parses"]
        resolution = resolve_payload(snapshot, parsed.intent, set())
        if resolution.errors:
            return resolution.errors
        rules = evaluate(
            RuleContext(parsed.intent, resolution, snapshot, self._policy, frozenset())
        )
        return [f"{r.rule}: {r.reason}" for r in rules if r.result is Outcome.BLOCK]

    # ------------------------------------------------------------------ helpers

    @staticmethod
    def _pool(recomps: list[Recomputation], resolution: Resolution) -> Verified:
        money = [rc.recomputed for rc in recomps if rc.recomputed is not None and not rc.is_percent]
        money += list(resolution.figures.values())
        money += [p.amount for p in resolution.payments]
        percents = [rc.recomputed for rc in recomps if rc.recomputed is not None and rc.is_percent]
        corrections = [
            (rc.model_figure, rc.recomputed, rc.is_percent)
            for rc in recomps
            if rc.status == "mismatch" and rc.model_figure is not None and rc.recomputed is not None
        ]
        return verified_pool(money, percents, corrections)

    def _present(
        self,
        intent: I.ActionIntent,
        recomps: list[Recomputation],
        prov: list[ProvenanceFinding],
        resolution: Resolution,
    ) -> tuple[list[dict[str, Any]], list[TextFix], list[str]]:
        pool = self._pool(recomps, resolution)
        claims: list[dict[str, Any]] = []
        fixes: list[TextFix] = []
        hits: list[str] = []
        for claim, rc, pf in zip(intent.claims, recomps, prov, strict=True):
            if pf.status == "removed":
                claims.append(
                    {
                        "statement": REMOVED.format(reason=pf.reason),
                        "figure": None,
                        "status": "removed",
                        "sources": [],
                    }
                )
                continue
            text, claim_fixes = check_text(
                f"claims[{rc.claim_index}].statement", claim.statement, pool
            )
            text, claim_hits = mask_guarantees(text)
            fixes += claim_fixes
            hits += claim_hits
            figure = None if pf.status == "figure_removed" else rc.recomputed
            claims.append(
                {
                    "statement": text,
                    "figure": None if figure is None else str(figure),
                    "status": {
                        "match": "verified",
                        "mismatch": "corrected",
                        "no_figure": "cited",
                        "not_recomputable": "figure_removed",
                    }[rc.status]
                    if pf.status != "figure_removed"
                    else "figure_removed",
                    "model_figure": str(rc.model_figure) if rc.status == "mismatch" else None,
                    "sources": list(claim.source_row_ids),
                }
            )
        return claims, fixes, hits

    def _receipt(
        self,
        receipt_id: str,
        intent_id: str,
        outcome: str,
        intent: I.ActionIntent | None,
        tier: Tier | None,
        now: datetime,
        *,
        flags: set[str],
        reads: set[str] | None = None,
        recomps: list[Recomputation] | None = None,
        prov: list[ProvenanceFinding] | None = None,
        rules: list[RuleResult] | None = None,
        policy: list[PolicyFinding] | None = None,
        text_fixes: list[TextFix] | None = None,
        resolution: Resolution | None = None,
        schema_errors: list[dict[str, str]] | None = None,
        disclosures: list[str] | None = None,
    ) -> TrustReceipt:
        needs_approval = outcome == "verified" and tier in (Tier.T2, Tier.T3)
        approval = Approval(
            required=needs_approval,
            by=APPROVERS if needs_approval else None,
            status="pending"
            if needs_approval
            else ("not_required" if outcome == "verified" else "not_applicable"),
            confirmation_phrase=t3_phrase(intent_id)
            if needs_approval and tier is Tier.T3
            else None,
            not_before=timestamp(now + timedelta(seconds=self._policy.t3.cool_off_seconds))
            if needs_approval and tier is Tier.T3
            else None,
        )
        return TrustReceipt(
            receipt_id=receipt_id,
            action_intent_id=intent_id,
            outcome=outcome,
            kind=None if intent is None else intent.kind.value,
            tier=None if tier is None else tier.value,
            rows_read=[{"table": str(table_of(r)), "id": r} for r in sorted(reads or ())],
            schema_errors=schema_errors or [],
            recomputations=[rc.as_dict() for rc in recomps or [] if rc.status != "no_figure"],
            provenance=[p.as_dict() for p in prov or []],
            rules_evaluated=[r.as_dict() for r in rules or []],
            policy=[p.as_dict() for p in policy or []],
            text_corrections=[f.as_dict() for f in text_fixes or []],
            payload_figures={
                k: str(v) for k, v in (resolution.figures if resolution else {}).items()
            },
            risk_flags=sorted(flags),
            disclosures=disclosures or [],
            approval=approval,
            verifier_version=VERIFIER_VERSION,
            created_at=timestamp(now),
        )


def _resolved(res: Resolution) -> dict[str, Any]:
    """The verifier's own figures, carried inside the sealed action for the Executor."""
    out: dict[str, Any] = {k: str(v) for k, v in res.figures.items()}
    if res.payments:
        out["payments"] = [
            {
                "invoice_id": p.invoice_id,
                "counterparty_id": p.counterparty_id,
                "amount": str(p.amount),
            }
            for p in res.payments
        ]
        out["pay_on"] = res.pay_on.isoformat() if res.pay_on else None
    return out


__all__ = ["VERIFIER_VERSION", "Decimal", "Verification", "Verifier", "t3_phrase"]
