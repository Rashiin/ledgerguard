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
from typing import Any

from ledgerguard.audit.chain import timestamp
from ledgerguard.config import Policy
from ledgerguard.domain.enums import Tier
from ledgerguard.facts import CHECK_ACTION, Fact, block_fact, facts_for_turn
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
from ledgerguard.verifier.text import (
    TextFix,
    Verified,
    check_text,
    find_figures,
    foreign_currency,
    verified_pool,
)
from ledgerguard.verifier.tiers import assign_tier

VERIFIER_VERSION = "1.0.0"
MALFORMED = "The agent produced a malformed action, so nothing was done."
APPROVERS = "operator"


def t3_phrase(intent_id: str) -> str:
    return f"CONFIRM {intent_id}"


@dataclass(frozen=True)
class TurnContext:
    """What deterministic code knows about the turn, recorded by the orchestrator.

    ``tools_called`` lists the tools the orchestrator actually ran. The model's
    output never sets it. The verifier uses it to decide which facts the turn
    could cite and which of them were mandatory.
    """

    tools_called: tuple[str, ...] = ()


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

    def verify(
        self, raw: str | bytes, snapshot: LedgerSnapshot, context: TurnContext | None = None
    ) -> Verification:
        context = context or TurnContext()
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
        model_claims = parsed.intent.claims
        facts = facts_for_turn(
            snapshot,
            parsed.intent.kind,
            context.tools_called,
            self._policy.rules.concentration_warning_share,
        )
        dry_run = CHECK_ACTION in context.tools_called
        claims, fact_errors, block_citations = _resolve_facts(model_claims, facts, dry_run)
        intent = parsed.intent.model_copy(update={"claims": claims})
        reads: set[str] = set()

        # ---- stage 2: recompute the claims, then what the payload would move
        recomps = [recompute_claim(snapshot, i, c, reads) for i, c in enumerate(intent.claims)]
        resolution = resolve_payload(snapshot, intent, reads)

        # ---- stage 4 runs before the rules need citations: provenance per claim
        prov = [check_claim(snapshot, i, c, recomps[i], reads) for i, c in enumerate(intent.claims)]
        for i, reason in fact_errors.items():
            prov[i] = ProvenanceFinding(i, "removed", reason)
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

        # ---- stage 4b: material omission. What mattered must have been said.
        blocks = {
            r.rule: block_fact(r.rule, r.reason, r.rows) for r in rules if r.result is Outcome.BLOCK
        }
        for i, rule in block_citations.items():
            if rule in blocks:
                prov[i] = ProvenanceFinding(i, "supported")
            else:
                prov[i] = ProvenanceFinding(
                    i, "removed", f"it cites a block by {rule}, and that rule did not block"
                )
        mandatory = {
            **{f"block.{k}": v for k, v in blocks.items()},
            **{k: f for k, f in facts.items() if f.mandatory},
        }
        omissions = _omitted(model_claims, prov, mandatory)
        for fact in omissions:
            reads.update(r for r in fact.source_row_ids if table_of(r))
        if omissions:
            flags.add("MATERIAL_OMISSION")

        # ---- stage 5: policy, and the text the user may see
        currency = snapshot.business.currency
        presented_claims, text_fixes, guarantee_hits = self._present(
            intent, recomps, prov, resolution, currency, mandatory
        )
        presented_claims = _lead_with_mandatory(presented_claims, omissions, mandatory)
        rationale, fixes = check_text(
            "rationale", intent.rationale, self._pool(recomps, resolution, mandatory), currency
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
        if any(f.reason.startswith("currency") for f in text_fixes) or any(
            foreign_currency(t, currency)
            for t in (intent.rationale, *(c.statement for c in intent.claims))
        ):
            flags.add("CURRENCY_MISMATCH")

        # ---- stage 6: tier
        needs_human = bool(flags & NEVER_AUTO_PASS)
        tier = assign_tier(intent.kind, needs_human)
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
            omissions=omissions,
            context=context,
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

    def dry_run(
        self, kind: str, payload: dict[str, Any], snapshot: LedgerSnapshot
    ) -> tuple[list[str], list[RuleResult]]:
        """Resolve a draft action's payload and run every rule on it, changing nothing.

        Returns payload errors and rule results. The proposer uses this before its
        final answer so the model can see a block and cite it. The approval-time
        re-check uses it too.
        """
        payload = {k: v for k, v in payload.items() if k != "resolved"}
        parsed = I.parse_intent(
            json.dumps({"kind": kind, "payload": payload, "claims": [], "rationale": "dry run"})
        )
        if parsed.intent is None:
            return ["the draft action does not parse"], []
        resolution = resolve_payload(snapshot, parsed.intent, set())
        if resolution.errors:
            return resolution.errors, []
        ctx = RuleContext(parsed.intent, resolution, snapshot, self._policy, frozenset())
        return [], evaluate(ctx)

    def recheck(self, kind: str, payload: dict[str, Any], snapshot: LedgerSnapshot) -> list[str]:
        """Re-run payload resolution and the rules against the ledger as it is now.

        This runs at approval time. An action verified earlier may no longer be
        safe, for example once another approved payment has used up the payroll
        cover. It returns the reasons the action must now be refused, and an
        empty list if it is still fine.
        """
        errors, rules = self.dry_run(kind, payload, snapshot)
        if errors:
            return (
                ["stored action no longer parses"]
                if errors == ["the draft action does not parse"]
                else errors
            )
        return [f"{r.rule}: {r.reason}" for r in rules if r.result is Outcome.BLOCK]

    # ------------------------------------------------------------------ helpers

    @staticmethod
    def _pool(
        recomps: list[Recomputation], resolution: Resolution, mandatory: dict[str, Fact]
    ) -> Verified:
        money = [rc.recomputed for rc in recomps if rc.recomputed is not None and not rc.is_percent]
        money += list(resolution.figures.values())
        money += [p.amount for p in resolution.payments]
        # Amounts in the verifier's own mandatory sentences, such as a block's
        # "short by" figure, were computed by the rules and count as verified.
        money += [
            fig.value
            for f in mandatory.values()
            for fig in find_figures(f.statement)
            if not fig.percent
        ]
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
        currency: str,
        mandatory: dict[str, Fact],
    ) -> tuple[list[dict[str, Any]], list[TextFix], list[str]]:
        pool = self._pool(recomps, resolution, mandatory)
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
                f"claims[{rc.claim_index}].statement", claim.statement, pool, currency
            )
            text, claim_hits = mask_guarantees(text)
            fixes += claim_fixes
            hits += claim_hits
            fact = mandatory.get(claim.fact_id or "")
            if fact is not None:
                # The verifier's own sentence carries the fact. The model's sentence
                # is kept as commentary beside it and can add, but not soften.
                claims.append(
                    {
                        "statement": fact.statement,
                        "figure": None if fact.figure is None else str(fact.figure),
                        "status": "verified",
                        "fact_id": fact.id,
                        "mandatory": True,
                        "commentary": text,
                        "sources": list(fact.source_row_ids),
                    }
                )
                continue
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
        omissions: list[Fact] | None = None,
        context: TurnContext | None = None,
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
            material_omissions=[
                {"fact_id": f.id, "inserted": f.statement, "why": f.materiality or ""}
                for f in omissions or []
            ],
            tools_called=list(context.tools_called) if context else [],
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


__all__ = ["VERIFIER_VERSION", "TurnContext", "Verification", "Verifier", "t3_phrase"]


def _resolve_facts(
    claims: list[I.Claim], facts: dict[str, Fact], dry_run: bool
) -> tuple[list[I.Claim], dict[int, str], dict[int, str]]:
    """Replace each fact citation with the fact's own derivation and rows.

    The model's statement is kept. Its figure is kept too when it gave one, so a
    wrong figure still shows up as a mismatch. A citation of a fact this turn did
    not produce is reported so provenance can remove the claim visibly.

    A citation of ``block.<RuleName>`` can only be checked after the rules run, so
    it is returned separately. It is valid only when the dry run ran this turn.
    """
    resolved: list[I.Claim] = []
    errors: dict[int, str] = {}
    blocks: dict[int, str] = {}
    for i, claim in enumerate(claims):
        if claim.fact_id is None:
            resolved.append(claim)
            continue
        if claim.fact_id.startswith("block."):
            if dry_run:
                blocks[i] = claim.fact_id.removeprefix("block.")
            else:
                errors[i] = f"it cites {claim.fact_id}, but no dry run ran this turn"
            resolved.append(claim)
            continue
        fact = facts.get(claim.fact_id)
        if fact is None:
            errors[i] = f"it cites fact {claim.fact_id}, which no tool in this turn produced"
            resolved.append(claim)
            continue
        derivation = (
            None
            if fact.formula is None
            else I.Derivation.model_construct(
                formula=fact.formula, operands=list(fact.operands), params=dict(fact.params)
            )
        )
        resolved.append(
            I.Claim.model_construct(
                statement=claim.statement,
                fact_id=claim.fact_id,
                figure=claim.figure if claim.figure is not None else fact.figure,
                derivation=derivation,
                source_row_ids=list(dict.fromkeys([*fact.source_row_ids, *claim.source_row_ids])),
            )
        )
    return resolved, errors, blocks


def _omitted(
    model_claims: list[I.Claim], prov: list[ProvenanceFinding], mandatory: dict[str, Fact]
) -> list[Fact]:
    """Mandatory facts the response left out. A fact counts as said only when a
    surviving claim cites its id."""
    said = {
        c.fact_id
        for c, p in zip(model_claims, prov, strict=True)
        if c.fact_id is not None and p.status != "removed"
    }
    return [f for f in mandatory.values() if f.id not in said]


def _lead_with_mandatory(
    claims: list[dict[str, Any]], omissions: list[Fact], mandatory: dict[str, Fact]
) -> list[dict[str, Any]]:
    """Mandatory facts come first, blocks before the rest, each once. Then the model's claims."""
    leading: dict[str, dict[str, Any]] = {}
    for c in claims:
        if c.get("mandatory"):
            leading.setdefault(c["fact_id"], c)
    for f in omissions:
        leading[f.id] = _inserted(f)
    rest = [c for c in claims if not c.get("mandatory")]
    return [leading[k] for k in mandatory if k in leading] + rest


def _inserted(fact: Fact) -> dict[str, Any]:
    return {
        "statement": fact.statement,
        "figure": None if fact.figure is None else str(fact.figure),
        "status": "inserted_by_verifier",
        "fact_id": fact.id,
        "mandatory": True,
        "sources": list(fact.source_row_ids),
    }
