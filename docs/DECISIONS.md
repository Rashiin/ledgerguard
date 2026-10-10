# Architecture decisions

Each entry: the decision, why, and what it costs. Newest last. This file is
the backbone of the technical documentation (Phase 8).

---

## D-001 · Money is `Decimal` in Python and integer cents in SQLite (Phase 1)

**Decision.** `ledgerguard.money` accepts only `Decimal` or `str`. It rejects
`float` and `int`: `5` is ambiguous between euros and cents. It also rejects
sub-cent precision. The `Money` column type stores integer cents and refuses
anything that is not a `Decimal`. Stored JSON (`CanonicalJSON`) refuses floats,
and non-money decimals such as rates are stored as exact text (`DecimalText`),
never SQLite `REAL`.

**Why.** Spec 5.2 and standing rule 2. Refusing floats where data enters the
system makes "no float in a monetary path" structural rather than a code-review
convention.

**Cost.** Every caller must quote amounts (`"5000.00"`), including in YAML
config.

## D-002 · Read-only connections everywhere except the Executor and provisioning (Phase 1)

**Decision.** `ledgerguard.db.engine` only hands out read-only engines. SQLite
is opened with `mode=ro`, and every connection runs `PRAGMA query_only=ON`. A
writable engine is built in exactly two places:

* the **Executor** (Phase 2): runtime ledger mutations;
* **`ledgerguard.provisioning`**: migrations and seeding. These run as a
  separate offline step before the application starts. The API, proposer and
  verifier never import this module.

**Why.** Spec 9.3, as revised: the guarantee is enforced by the connection,
not by convention or a static check. Provisioning must write, and it happens
before the application exists, so it gets its own named, test-guarded door
rather than borrowing the Executor's.

**Cost.** Phase 2 adds a test that no module other than these two constructs
a writable engine, and that the application never imports the provisioning
or seed modules. This is the one write path outside the Executor. It is a
deliberate, documented exception and will be listed in the security
self-assessment.

## D-003 · Schema additions beyond spec 5.1 (Phase 1)

| Addition | Why |
|---|---|
| `business.data_as_of` | The ledger's "today". Projections are relative to it, never the wall clock, so the demo is reproducible on any date. |
| `account.credit_limit` | Size of the revolving facility. Needed for the "draw €50k on the credit line" scenario and the financing matcher. |
| `invoice.description` | Free text on invoices. This is the untrusted content the injection fixtures live in (scenario 11). |
| `transaction.invoice_id`, `transaction.payroll_run_id` | Settlement links, so a recomputation can walk from a payment to its source row. |
| `obligation.description` | Human label for forecast lines. |
| `financing_product.name` | Product display name. |
| Status `scheduled`/`paid` on `payroll_run` | The spec lists `status` without values. |

`account.floor` is **not** a column. Floors live in `config/policy.yaml` as
policy, not ledger data.

## D-004 · Obligations are materialised occurrences (Phase 1)

**Decision.** Each future obligation is one row with its own `due_on`.
`recurrence_rule` keeps the RFC 5545 rule of the series it belongs to, for
display. Past obligations are not rows; they appear as transactions.

**Why.** The projection, the rules (`TaxReserveFloor`, `PayrollProtection`)
and the Trust Receipt all need a concrete row id to cite. Expanding a
recurrence rule at query time would give claims nothing stable to point at.

## D-005 · The 13-week projection model (Phase 1, used by Phase 5)

**Decision.**

* **Opening:** cash on all bank and cash accounts at `data_as_of`. Credit
  lines are headroom, not cash.
* **Receivables:** each open receivable (`draft`, `sent`, `part_paid`,
  `overdue`) is expected on its due date plus that customer's historical mean
  lateness, in whole days rounded half up. `disputed` receivables are
  excluded.
* **Outflows:** payables, scheduled payroll and obligations on their due dates.
* **Already past due:** anything past due lands in week 1.
* **Week 1** starts on the Monday after `data_as_of`.
* **`draft` invoices are the confirmed order book:** confirmed sales orders
  and confirmed purchase orders not yet invoiced. They are projected as
  `EXPECTED` (D-009), never as `CONTRACTED`.

**Why.** Without forward-dated orders, any 13-week projection declines
structurally, because supplier terms (45-60 days) are longer than customer
terms. A real 13-week cash forecast always includes the order book.

**Cost / known limitation.** Variable spend with no invoice or obligation row
(fuel, utilities, insurance, bank fees; about €4k a month) is not projected.
This is listed in fixtures/README.md and will go in the risk register.

## D-006 · The seeder places the shortfall by solving for it (Phase 1)

**Decision.** The seeder runs the application's own projection and solves, in
closed form, for:

* the as-of cash;
* the size of the Brennan refit milestone;
* if a seed needs it, a week-6 stock instalment.

These are chosen so that weeks 6-8, and only those, close below zero. The
opening balance is then derived from the history. A late equipment purchase is
enlarged if the history would otherwise be overdrawn.

**Why.** Spec 5.3 requires the hooks to exist "by construction". Random
week-to-week noise (about ±€100k) is the same size as any hand-tuned trough,
so a fixed design only worked for some seeds. With solving, every seed tested
(44 of them) calibrates. Seed 42 is the demo seed.

**Cost.** The refit milestone (€158k net for seed 42) is large relative to the
business. It is framed as Brennan's 12-kitchen refit programme, which also
makes the demo coherent: stock for the refit is paid in week 7, and Brennan
pays late in week 9.

## D-007 · Invoice numbering (Phase 1)

**Decision.** Invoices are numbered by issue date, then `inv_0412` and
`inv_0377` are swapped onto the planted rows the demo script names.

**Why.** The spec's scenarios name these two numbers. Numbers are opaque
identifiers, and the swap is documented in fixtures/README.md.

## D-008 · uv-managed Python 3.11; Docker is the judges' path (Phase 1)

**Decision.**

* Python 3.11 is installed and pinned by uv (`.python-version`, `uv.lock`).
  It needs no system Python, no Homebrew and no admin rights.
* The Docker image builds the seeded ledger in and defaults to
  `LLM_CLIENT=replay`. It contains no model server.

**Why.** Spec 4.2. Judges get the whole demo from `docker compose up` with no
network. Development against a local model happens on the host.

**Cost.** The Docker path is written in Phase 1 but only verified in Phase 7,
when the Docker daemon is available.

## D-009 · Every projected line has a confidence class; classes are never merged (Phase 1 addendum)

**Decision.** Each `ExpectedFlow` carries one class:

* **`CONTRACTED`:** issued invoices, scheduled payroll and obligations.
* **`EXPECTED`:** draft invoices, i.e. the confirmed order book.
* **`SPECULATIVE`:** a seasonal fill after the order book ends.

The fill assumes each counterparty repeats what it invoiced in the same week
last year (52 weeks earlier, same terms, usual lateness, no growth
adjustment). Every fill line cites the historical invoice it repeats.

Each projected week reports inflow and outflow per class, and three nested
closing balances: contracted only, contracted plus expected, and all classes.
The seeder's order book now reaches about five weeks ahead (to 2026-11-20).
Further out, the projection is visibly speculative instead of silently
complete.

**Why.** D-005 counted the order book in the forecast, which is right but
blended signed commitments with modelled run-rate into one number. That is
the most common way a 13-week forecast misleads an owner, and the first thing
a finance-literate reader checks. Separating the classes makes our biggest
modelling assumption visible and checkable. For seed 42 the full model
recovers from week 9, but contracted-only cash stays negative from week 6 to
week 13. The UI and alerts must show both.

**Cost.**
* The fill is the crudest part of the model: no growth, no trend, and one-off
  orders from last year are repeated.
* Contracted-only means contracted lines on *both* sides: the same standard of
  evidence on each side, and the primary band. Phase 5 adds one named stress
  scenario, "contracted inflows against all outflows". Payroll, tax, rent and
  loan repayments fall due whether or not the order book converts. It is a
  scenario, not a fourth band.
* Contracted-only cash will go negative within 13 weeks for any realistic
  SME. Nobody holds a quarter of signed orders. Docs and UI must say so in
  one line, so the band reads as honesty rather than a modelling failure.
* Alerts (Phase 5) must name the classes that drive them, and the shortfall
  headline must also be quoted on contracted-only cash.

## D-010 · The Executor accepts only sealed `VerifiedAction`s (Phase 2)

**Decision.** A `VerifiedAction` carries an HMAC-SHA256 seal over its
canonical content: intent id, kind, tier, payload, receipt id, verifier
version, verification time and blocking rules. The key is 32 random bytes in
`data/verifier.key` (mode 0600), created on first start and never committed.

The Executor recomputes the seal and refuses:
* anything that is not a `VerifiedAction`;
* a missing or foreign seal;
* any field changed after sealing, including in-place payload edits.

**Why.** An `isinstance` check alone would let any in-process code build a
`VerifiedAction` by hand. The seal also protects actions waiting in the
approval queue: a pending T3 payment edited in the database (amount or
payee) no longer verifies.

**Cost / residual risk.** Code running inside the application process can
read the key. The seal guards against accidental bypass and tampering with
stored actions, not against a compromised process. This goes in the risk
register.

## D-011 · The Executor is the single write gateway, including for the audit log (Phase 2)

**Decision.** The Executor owns the only runtime writable engine. It exposes:
* `execute(action, approval)` for ledger changes;
* `record(actor, type, payload)` for audit appends.

Other components log events through `record`. A ledger change and its
`action.executed` event (with a before/after field diff) commit in one
transaction. Every refusal is written as `action.refused`, with the received
type and the reason.

The Executor also checks, independently of the verifier:
* `BLOCK` cannot be overridden;
* `INITIATE_PAYMENT` and `ACCEPT_FINANCING_OFFER` must be T3;
* T2 needs a human approver (operator or owner; never `llm` or `system`);
* T3 additionally needs the typed phrase `CONFIRM <intent_id>` and an
  approval no earlier than `verified_at + cool_off_seconds`, checked against
  both the approval timestamp and the clock.

Writers use `BEGIN IMMEDIATE`, so audit sequence numbers cannot race.

**Why.** One writer is easy to reason about and test. These checks repeat
verifier checks on purpose (defence in depth): if Phase 3 has a bug, the
Executor still holds the hard invariants.

**Cost.** The Executor duplicates part of the tier logic. Kinds without a
handler are refused ("no executor handler"). Phase 2 implements
`MARK_DISPUTED`; the others come with their payload schemas in Phases 3 and 5.

## D-012 · Audit chain: hash definition and append-only trigger (Phase 2)

**Decision.**
* `hash = sha256(prev_hash ‖ canonical_json(content))`, where content is
  `{id, seq, occurred_at, actor, event_type, payload}`.
* The genesis `prev_hash` is 64 zeros.
* Timestamps are hashed in exactly the textual form stored (UTC, microseconds).
* Verification checks contiguous sequence numbers from 1, each `prev_hash`
  link, and each recomputed hash. It reports the first bad sequence number
  and why.
* Migration 0002 adds SQLite triggers that abort any UPDATE or DELETE on
  `audit_event`.

**Why.** Two independent layers:
* the trigger stops modification through any normal connection;
* the chain detects modification by someone who drops the trigger, which
  anyone with file access can do.

The tests tamper exactly that way.

**Cost / residual risk.** The chain is unkeyed, as specified. An attacker
with file access alone who rewrites an event and *every later hash* produces
a chain that is internally consistent. The chain proves integrity only
relative to a head hash recorded elsewhere, so the evidence export publishes
it.

The accurate description is "tamper-evident with a published head hash". It
is not tamper-proof.

## D-013 · Row drill-down endpoint (Phase 2)

**Decision.** `GET /api/ledger/rows/{table}/{id}` returns one row from an
allow-listed table. Decimals are returned as strings, never floats.

**Why.** Spec 7.3 requires every figure to drill down to its source rows in
one click. The Trust Receipt cites `{table, id}` pairs, and this endpoint
resolves them. It is an addition to the endpoint list in spec 8.1.

## D-014 · Stage 1 parses JSON text only; money must be quoted (Phase 3)

**Decision.** `parse_intent` accepts only JSON text. A bare JSON number with
a fraction is refused before Pydantic sees it, and money fields refuse ints,
so `"46854.00"` is the only accepted form. Unknown fields anywhere reject the
intent.

The one exception is a top-level `tier`. It is removed and recorded as
`MODEL_TIER_IGNORED` rather than failing the whole action. The spec says the
model's tier is ignored, which is different from treating it as malformed.

The payload is validated against the model for its `kind` in a second step,
so error locations stay exact (`payload.amount`).

**Why.** A JSON number has already passed through binary floating point by
the time it is parsed. Strictness here is what makes "never repair" possible.

## D-015 · Payment payloads carry no amounts (Phase 3)

**Decision.** `INITIATE_PAYMENT` names invoices only. The verifier computes
what is owed (gross minus paid) and seals it into `payload.resolved`.
`RECORD_PAYMENT` takes an optional amount, which must be between €0.01 and
the outstanding balance; leaving it out means paid in full.

At execution, the Executor re-checks that each invoice still owes exactly the
sealed amount, and refuses with "changed since verification" if not.

**Why.** Spec 5.2: the model is never asked for a final monetary figure. The
model has no field through which it could name a sum to pay.

## D-016 · Claims: a closed formula registry; uncited claims are stripped, not rejected (Phase 3)

**Decision.**
* A claimed figure needs a `derivation`: one of nine named formulas, with
  operand row ids. The verifier recomputes it, and the recomputed value
  always replaces the model's. A difference raises `FIGURE_MISMATCH` and both
  values go on the receipt.
* A figure without a derivation, or one that cannot be recomputed, is
  removed (`UNVERIFIED_FIGURE`); its statement stays.
* Claims with no citations pass the schema. The provenance stage replaces
  them with a visible "[unsupported claim removed by verifier: …]" marker.
* Cited rows must come from a table that can support the formula.

**Why.** Spec 6.1 says `source_row_ids` "MUST be non-empty", and stage 4
says uncited assertions are stripped visibly. Rejecting the whole intent for
one uncited sentence would hide the verifier's work instead of showing it.

**Cost.** New capabilities (Phase 5: total cost of capital, DSO and so on)
must add formulas before the model can make claims about them.

## D-017 · Numbers inside prose must match a verified figure (Phase 3)

**Decision.** After recomputation, every money-like number in claim
statements and the rationale is checked against the verified figures: the
recomputed claim figures and the verifier's payload figures. "Money-like"
means it has a currency mark, a %, decimals, thousands separators, a k or m
suffix, or is at least 1,000 and not a year.

* A number equal to a corrected model figure is replaced with the recomputed
  value.
* Any other unmatched number becomes "[figure removed: not verified]".

Matching respects the precision the text shows ("€46.9k" matches €46,854.00).
Row ids, dates, years and small counts are ignored.

**Why.** Otherwise a model could put a wrong amount in a sentence while
giving the right figure in its claim. "Recomputes every number" has to cover
the sentence too.

**Cost / residual risk.** Detection is a pattern match. Numbers written in
words ("forty thousand") are not caught (risk register R-09).

## D-018 · Protection rules judge projected cash on contracted + order-book lines (Phase 3)

**Decision.** PayrollProtection and TaxReserveFloor evaluate projected cash
on the policy's `protection_band`, which defaults to `with_expected`
(CONTRACTED + EXPECTED). The speculative seasonal fill can never justify a
payment.

* **PayrollProtection** blocks an outbound action that leaves less than the
  next payroll's employer cost, at the lower of its lead-day check point and
  the eve of payday. It warns when the margin is under 20%.
* **TaxReserveFloor** blocks an outbound action that leaves less cash on the
  eve of any tax due within 45 days than that tax. Paying a not-yet-due
  invoice early can trip it, by moving cash ahead of the VAT date.

**Why.** Judged on contracted-only cash, which goes negative within 13 weeks
for any real SME (D-009), the rules would block almost every payment. Judged
on the full model, they would lean on numbers that are only modelled.

**Cost.** If the order book does not convert, the protection is weaker than
it looks (R-12). The band is configurable, and `contracted` is the stricter
setting.

## D-019 · Approval re-checks the rules against the current ledger; actions run once (Phase 3)

**Decision.**
* When a pending T2 or T3 action is approved, the service re-runs payload
  resolution and every rule against a fresh snapshot. If anything now
  blocks, the intent is closed as `rejected` with the reasons, and nothing
  executes.
* The Executor runs a sealed action only if its intent is on record with
  status `verified` or `pending_approval`. It then marks it `executed`, so
  replaying the same sealed action is refused.
* A handler refusal marks the intent `failed`.

**Why.** Without the re-check, two payments that each pass PayrollProtection
when verified could be approved one after the other and jointly starve
payroll. That would be a way to reach a BLOCK through approvals. A test
proves it is closed.

## D-020 · Migration 0003 rebuilds tables from explicit definitions (Phase 3)

**Decision.** Making `action_intent.kind` and `trust_receipt.tier` nullable
requires a table rebuild on SQLite. Migration 0003 writes the new tables out
in full (create, copy, drop, rename) instead of using Alembic batch mode.

**Why.** Batch mode reflects the existing table, and reflected constraint
order differed between the first and later runs in one process. That broke
the byte-identical seed. The test now provisions three times in one process.

## D-021 · Financing policy hook ahead of the financing maths (Phase 3)

**Decision.** Any financing content (RANK_FINANCING, ACCEPT_FINANCING_OFFER,
or a claim citing a `financing_product`) gets the non-removable disclosure.
It is blocked unless every product it discusses has a verified
total-cost-of-capital claim. The formulas that qualify are listed in
`TOTAL_COST_FORMULAS`, which is empty until Phase 5 adds the cost
calculator.

**Why.** The policy check belongs to Phase 3. The cost model belongs to the
Financing Matcher. Until Phase 5, financing recommendations are blocked by
design, which is the safe direction.

## D-022 · One outbound ceiling replaces the per-tier ceilings (Phase 3)

Spec 6.3 now says ceilings limit money leaving the business and apply only to outbound actions. The only outbound kinds are INITIATE_PAYMENT and ACCEPT_FINANCING_OFFER, and both are always T3. A ceiling can therefore never escalate a tier, and the old T1 and T2 ceilings had nothing left to do.

We chose a single `outbound_ceiling` in `config/policy.yaml`. An outbound amount above it is blocked by AmountCeiling. Recording a receipt stays T2 with no ceiling and is bounded by what the invoice still owes, re-checked at execution.

We gave up escalation for large drafts. A €900,000 draft invoice is now T1 like any other draft. That is acceptable because a draft moves no money and is not sent to anyone.

This would change if drafts started leaving the system, for example by being emailed to customers. Then a draft would be an external effect and would need its own tier and ceiling.
