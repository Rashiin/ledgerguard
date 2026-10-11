# Decisions

Each entry records a choice that shaped the system. Where the choice was a judgement call, the entry says what was chosen, what was given up and what would change the answer. Newest entries are last.

## D-001 · Money is Decimal in Python and integer cents in SQLite

`ledgerguard.money` accepts a `Decimal` or a string and nothing else. It refuses floats because most cent amounts have no exact binary form. It refuses integers because `5` could mean five euros or five cents. It refuses sub-cent precision instead of rounding it away. The `Money` column stores integer cents. Stored JSON refuses floats. Rates are stored as exact text, never as SQLite `REAL`.

Refusing floats at every entry point makes the rule against floating-point money a property of the code rather than something a reviewer has to catch. The cost is that every amount must be quoted, including in YAML.

## D-002 · Read-only connections everywhere except the Executor and provisioning

`ledgerguard.db.engine` hands out read-only engines only. SQLite is opened with `mode=ro` and each connection runs `PRAGMA query_only=ON`. Writable engines are built in two places. The Executor makes runtime ledger changes. `ledgerguard.provisioning` runs migrations and the seeder as a build step before the app starts.

We chose to give provisioning its own writable path instead of routing it through the Executor. Seeding happens before any verifier or approval exists, and forcing it through the Executor would have meant a fake verification for every seeded row.

We gave up a single writer in the codebase. The exception is named, a test proves the running app never loads provisioning code, and the security self-assessment lists it. If the seeder ever had to run while the app was live, this would have to change.

## D-003 · Columns added to the required schema

| Addition | Reason |
|---|---|
| `business.data_as_of` | The date the ledger treats as today. Projections use it instead of the wall clock, so the demo gives the same answer on any day. |
| `account.credit_limit` | The size of the revolving facility, needed by the credit-line scenario and the financing matcher. |
| `invoice.description` | Free text on invoices. The injection fixtures hide instructions here. |
| `transaction.invoice_id` and `transaction.payroll_run_id` | Links from a payment to what it settles, so recomputation can walk back to the source row. |
| `obligation.description` | A label an owner can read on a forecast line. |
| `financing_product.name` | The product's display name. |
| `scheduled` and `paid` as payroll statuses | The requirements name the column but not its values. |

Account floors are not a column. They are policy and live in `config/policy.yaml`.

## D-004 · Each future obligation is its own row

Every future obligation has one row with its own due date. `recurrence_rule` keeps the rule of the series it belongs to for display. Past obligations appear as transactions.

The projection, the rules and the Trust Receipt all cite row ids. A recurrence expanded at query time would give a claim nothing stable to point at. The cost is that the seeder must write each occurrence up to the end of the horizon.

## D-005 · The 13-week projection model

Opening cash is the balance of the bank and cash accounts on `data_as_of`. Credit lines count as headroom, not cash. Each open receivable is expected on its due date plus that customer's mean lateness in the past, in whole days rounded half up. Disputed receivables are left out. Payables, payroll and obligations are expected on their due dates. Anything past due lands in week 1. Week 1 starts on the Monday after the ledger date.

We chose to count the confirmed order book. Without it any 13-week projection falls steadily, because suppliers give 45 to 60 days and customers 30, and a real cash forecast always includes known orders. The order book is projected as `EXPECTED`, never as `CONTRACTED`. See D-009.

We left out variable spend that has no invoice or obligation row: fuel, utilities, insurance and bank fees, about €4,000 a month. Including it would need a run-rate model on the outflow side, and we preferred a forecast in which every line cites a row. The projection is optimistic by that amount, which the risk register records as R-05. A ledger with large untracked spend would change this.

## D-006 · The seeder solves for the shortfall instead of hoping for it

The seeder runs the application's own projection and solves for the cash on hand, the size of Brennan's refit milestone and, when a seed needs it, a first instalment on the refit stock. It picks values that put weeks 6 to 8 at least €20,000 below zero and every other week at least €18,000 above it. It then sets the opening balance 24 months earlier so the history arrives at that cash. A late equipment purchase grows if the history would otherwise be overdrawn.

The requirements say the planted situations must exist by construction. Week-to-week noise in the generated data is about €100,000, which is as large as any hand-tuned trough, and a fixed design worked for some seeds and failed for others. Solving made every one of the 43 seeds in the last sweep work.

The cost is a refit milestone that is large for the business, €185,000 net for seed 42. We framed it as Brennan's 12-kitchen refit programme. That framing also makes the demo coherent: stock for the refit is paid in week 7 and Brennan pays late, in week 9.

## D-007 · Invoice numbers

Invoices are numbered in issue order, then `inv_0412` and `inv_0377` are swapped onto the planted rows that the demo script names. Numbers are identifiers, and the swap is documented in fixtures/README.md. The cost is that numbering is not strictly chronological.

## D-008 · uv manages Python, and Docker is for judges

uv installs and pins Python 3.11 and the lock file. That needs no system Python, no Homebrew and no admin rights. The Docker image builds the seeded ledger in and starts with `LLM_CLIENT=replay`, so a judge gets the whole demo with no model server and no network. Development against a local model happens on the host. The Docker path is written now and verified in Phase 7, when a Docker daemon is available.

## D-009 · Every projected line has a confidence class, and classes are never merged

Every projected line is `CONTRACTED`, `EXPECTED` or `SPECULATIVE`. Contracted lines are issued invoices, scheduled payroll and obligations. Expected lines are draft invoices from the confirmed order book. Speculative lines are a seasonal fill after the order book ends. Each week reports inflow and outflow per class and three running balances: contracted only, contracted plus expected, and all classes. The seeder's order book reaches five weeks ahead, to 20 November.

Counting the order book was right, but blending it with signed commitments hid the main modelling assumption inside one number. A finance reader checks that first. Separating the classes makes the assumption visible. For seed 42 the full model recovers from week 9 while contracted-only cash stays negative from week 6 to week 13.

The seasonal fill assumes each counterparty repeats what it invoiced in the same week last year, on the same terms, paid with its usual lateness. Each fill line cites the invoice it repeats. We chose this over a statistical forecast because an owner can check it by hand. We gave up growth and trend, and last year's one-off orders are repeated. A ledger with strong growth would make this fill too pessimistic and would justify a trend term.

Contracted-only means contracted lines on both sides. It applies the same standard of evidence to money in and money out, which makes it the primary band. A stress view that sets contracted inflows against all outflows comes in Phase 5 as a single named scenario, because payroll, tax, rent and loan repayments fall due whether or not orders convert.

Contracted-only cash goes negative within 13 weeks for any realistic small business, because nobody holds a quarter of signed orders. The docs and the UI say so in one line so the band reads as honesty, not as a broken dataset.

## D-010 · The Executor accepts only sealed actions

A `VerifiedAction` carries an HMAC-SHA256 seal over its full content: intent id, kind, tier, payload, receipt id, verifier version, verification time and blocking rules. The key is 32 random bytes in `data/verifier.key`, readable only by the owner and never committed. The Executor recomputes the seal and refuses anything that is not a `VerifiedAction`, has no seal or a foreign seal, or was changed after sealing.

A type check alone would let any code in the process build a `VerifiedAction` by hand. The seal also protects actions waiting for approval. A pending payment edited in the database no longer verifies.

We gave up protection against a compromised application process, which could read the key. That is out of scope for a single-machine sandbox and is listed as R-02. A deployment with separate verifier and executor processes would move the key to the verifier side only and give the Executor a verification key.

## D-011 · The Executor is the only writer, including for the audit log

The Executor owns the only writable engine the running app has. It exposes `execute` for ledger changes, `store_verification` for intents and receipts, `decline` and `close` for ending pending actions, and `record` for audit events. A ledger change, the intent's new status and its audit event commit together or not at all. Every refusal is written to the log.

The Executor repeats some verifier checks on purpose. It refuses blocked actions, payments that are not T3, T2 actions without a human approver, and T3 actions without the typed confirmation or before the cool-off ends. If the verifier had a bug, the hard invariants would still hold. The cost is a little duplicated tier logic. Writers use `BEGIN IMMEDIATE`, so audit sequence numbers cannot race.

## D-012 · How the audit chain is hashed and guarded

Each event's hash is `sha256(prev_hash ‖ canonical_json(content))`, where content is the id, sequence number, timestamp, actor, type and payload. The first event links to 64 zeros. Timestamps are hashed in exactly the text form that is stored. Verification checks that sequence numbers run from 1 without gaps, that each link matches and that each hash recomputes, and it reports the first event that fails. Migration 0002 adds triggers that refuse UPDATE and DELETE on the log.

The triggers stop changes through any normal connection. The chain detects changes made by someone who drops the triggers, which anyone with file access can do, and the tests tamper exactly that way.

We kept the chain unkeyed, as the requirements define it. A keyed chain would make a consistent rewrite need the key as well as file access. We gave that up for a format anyone can verify with `sha256` and nothing else. The log is therefore tamper-evident with a published head hash, not tamper-proof, and the evidence export publishes the head hash. If the log had to stand alone without an external anchor, we would key it.

## D-013 · An endpoint for single source rows

`GET /api/ledger/rows/{table}/{id}` returns one row from an allow-listed table, with decimals as strings. The requirements say every figure must trace back to its rows in one click, and the Trust Receipt cites rows by table and id. This endpoint resolves those citations. It is an addition to the required endpoint list.

## D-014 · Stage 1 parses JSON text and requires quoted money

`parse_intent` accepts JSON text only. A bare JSON number with a fraction is refused before Pydantic sees it, and money fields refuse integers, so `"46854.00"` is the only accepted form. Unknown fields anywhere reject the intent. The payload is validated against its kind in a second step so that error locations stay exact, such as `payload.amount`.

A top-level `tier` is the one exception. It is removed and recorded as `MODEL_TIER_IGNORED` instead of rejecting the whole intent. The requirements say the model's tier is ignored, and ignoring something is different from treating it as malformed. If models started using the tier field to probe the system, rejecting it outright would be the stricter choice.

## D-015 · Payment payloads carry no amounts

`INITIATE_PAYMENT` names invoices and nothing else. The verifier computes what each invoice still owes and seals that into the action. `RECORD_PAYMENT` takes an optional amount between €0.01 and what is owed, and leaving it out means paid in full. When the action runs, the Executor checks again that each invoice still owes exactly the sealed amount and refuses otherwise.

The requirements say the model is never asked for a final monetary figure. Leaving the field out of the schema means the model has nowhere to put one. Part payments to suppliers are not possible in this version. Supporting them would add an amount that a person types during approval, never one the model supplies.

## D-016 · Claims use a closed set of formulas, and uncited claims are stripped

A claimed figure needs a derivation that names one of nine formulas and its operand rows. The verifier recomputes it, and the recomputed value always replaces the model's. A figure with no derivation, or one that cannot be recomputed, is removed while its statement stays. Cited rows must come from a table that can support the formula.

Claims with no citations pass the schema and are replaced in what the user sees by a marker saying the verifier removed them. The requirements say citations must be present and also that uncited assertions are stripped visibly. Rejecting the whole intent for one uncited sentence would hide the verifier's work, so we chose stripping.

The cost is that each new capability must add its formulas before the model can make claims about it. Phase 5 adds total cost of capital and the analysis ratios.

## D-017 · Numbers inside sentences must match a verified figure

Every money-like number in claim statements and the rationale is checked against the figures the verifier computed. A number counts as money-like if it has a currency mark, a percent sign, decimals, thousands separators or a k or m suffix, or if it is at least 1,000 and is not a year. A number equal to a corrected model figure becomes the recomputed value. Any other unmatched number becomes "[figure removed: not verified]". Matching respects the precision the text shows, so "€46.9k" matches €46,854.00. Row ids, dates, years and small counts are ignored.

Without this, a model could give the right figure in its claim and a wrong one in its sentence. We chose pattern matching because it is predictable and testable. We gave up numbers written in words, such as "forty thousand", which are not caught and are listed as R-09. A model that wrote amounts in words would justify a number-word parser.

## D-018 · Payroll and tax rules judge contracted lines plus the order book

PayrollProtection and TaxReserveFloor judge projected cash on the band named by `protection_band` in policy, which defaults to contracted plus expected. The speculative fill never justifies a payment.

PayrollProtection blocks a payment that leaves less than the next payroll needs, at the lower of two points: five days before pay day and the day before pay day. It warns when the margin is under 20% of the payroll. TaxReserveFloor blocks a payment that leaves less cash on the eve of a tax due within 45 days than that tax. Paying an invoice early can trip it by moving cash ahead of the VAT date, and a test shows that case.

Judged on contracted-only cash, which goes negative within 13 weeks for any real business, these rules would block nearly every payment. Judged on the full model, they would lean on numbers that are modelled. We chose the middle band and gave up protection against an order book that fails to convert, recorded as R-12. Setting the band to `contracted` is the stricter option, and a business with an unreliable order book should use it.

## D-019 · Approval re-runs the rules, and a sealed action runs once

When a pending action is approved, the service resolves its payload again and runs every rule on a fresh snapshot of the ledger. If anything now blocks, the intent is closed as rejected with the reasons, and nothing runs. The Executor runs an action only if its intent is on record as verified or pending, and marks it executed in the same transaction, so the same sealed action cannot be replayed. A refusal from a handler marks the intent failed.

Without the re-check, two payments that each pass PayrollProtection could be approved one after the other and together starve payroll. That would reach a blocked outcome through approvals, which the requirements forbid. A test proves the gap is closed. The cost is a second snapshot load at approval time.

## D-020 · Migration 0003 rebuilds tables from written-out definitions

Making `action_intent.kind` and `trust_receipt.tier` nullable needs a table rebuild on SQLite. Migration 0003 writes the new tables out in full, copies the rows, drops the old tables and renames the new ones. Alembic's batch mode reflects the existing table instead, and the reflected constraint order differed between the first and later runs in one process. That broke the byte-identical seed. The seed test now provisions three times in one process to catch this.

## D-021 · The financing policy check exists before the financing maths

Financing content is any RANK_FINANCING or ACCEPT_FINANCING_OFFER intent, or any claim that cites a financing product. It always carries the fixed disclosure. It is blocked unless every product it mentions has a verified total-cost-of-capital claim. The formulas that count are listed in `TOTAL_COST_FORMULAS`, which stays empty until Phase 5 adds the cost calculator.

We chose to put the policy check in Phase 3 with the other stages and let it block until the calculator exists. Until Phase 5, financing recommendations are blocked by design, which fails in the safe direction.

## D-022 · One outbound ceiling replaces the per-tier ceilings

The amount ceiling exists to limit money leaving the business, so it applies only to outbound actions. The only outbound kinds are INITIATE_PAYMENT and ACCEPT_FINANCING_OFFER, and both are always T3. A ceiling can therefore never escalate a tier, and the old T1 and T2 ceilings had nothing left to do.

We chose a single `outbound_ceiling` in `config/policy.yaml`. An outbound amount above it is blocked by AmountCeiling. A €200,000 supplier payment is ordinary for some businesses, so a bare refusal would be a dead end. The refusal names the limit and says it is set by `outbound_ceiling` in `config/policy.yaml`, so the owner knows how to raise it. Recording a receipt stays T2 with no ceiling and is bounded by what the invoice still owes, re-checked at execution.

We gave up escalation for large drafts. A €900,000 draft invoice is now T1 like any other draft. That is acceptable because a draft moves no money and is not sent to anyone.

This would change if drafts started leaving the system, for example by being emailed to customers. Then a draft would be an external effect and would need its own tier and ceiling.

## D-023 · The model runtime enforces the schema

`LocalClient` passes a JSON schema as Ollama's `format`, and Ollama turns it into a grammar. The schema is written by hand and built per turn from the allowed kinds. Money is a quoted string with at most two decimals, payments have no amount field, and kinds outside the allowlist cannot be expressed. A test checks that the schema, the worked examples and the Pydantic models agree.

In the first probe the model held the shape without help, but a 7B model will not always do so, and enforcing the schema removes that failure class. The parser stays as strict as before, so a runtime that ignores the schema still cannot slip anything through. We gave up nothing we needed. A runtime without grammar support would fall back to plain JSON mode and the retry path.

## D-024 · Tools return facts, and the model cites them by id

In the first probe all three of the model's errors came from building derivations: an invented row id, a missing field name and the wrong week. Tools now return facts, each with an id, a statement written by code, a figure and the derivation behind it. The model cites a fact by id and phrases the sentence. The verifier recomputes the turn's facts from its own snapshot and resolves each id there, so a fact the model echoes back is never trusted. A citation of a fact no tool produced this turn is removed visibly.

We gave up some of the model's freedom to compute things nobody prepared for it. The model can still write a claim with its own derivation, and the verifier checks it as before. If the model ever needed figures no tool provides, the right answer would be a new tool, not wider derivations.

In the re-probe the fact-based prompt was half the size of the first one. The turn took 14 seconds instead of 40, and every figure matched.

## D-025 · Stage 4b, material omission

Correct statements can still mislead by leaving out the one thing that mattered. The first probe gave three plausible claims about the quarter and never mentioned that week 7 closes at minus €219,676.

The deterministic layer declares when a fact is mandatory. The conditions are a projected week below zero, a BLOCK on the action, a customer above the concentration threshold in a revenue answer, and high-severity alerts once Phase 5 builds them. If no surviving claim cites a mandatory fact, the verifier puts it at the top of the answer and records `MATERIAL_OMISSION`.

We chose to deliver the corrected answer with the omission flagged rather than refuse the turn. That matches how a wrong figure is handled: the verifier's version wins and the receipt says what changed. We also chose citation as the test of "said", because matching words in prose would be guesswork. The cost is that a model which mentions the shortfall in passing without citing it gets the fact inserted anyway. That is redundant, but never wrong.

A BLOCK is mandatory, but the model cannot know about a block before it proposes the action. Until the turn loop gives the model a dry-run tool that can cite `block.<RuleName>`, every blocked action carries the flag. The receipt is still honest about it.

Which facts are mandatory depends on the tools the turn ran. The orchestrator records that list, not the model. Forecasts, scenarios and financing rankings always count as cash answers, so skipping the projection tool cannot dodge the shortfall. If the orchestrator failed to record a tool, a revenue answer could miss its concentration fact. That is R-17.

## D-026 · Currency is its own finding

The first probe wrote dollars for a euro ledger. Any figure or mention in another currency raises `CURRENCY_MISMATCH`. A matching amount is shown again in euros and the change is recorded. A non-matching amount is masked with its symbol, so a stray "$" never survives next to a masked figure. A model that writes the wrong currency in a financial answer has misunderstood the ledger, and the flag lets an owner see that. "Pound" on its own is not treated as a currency, because it is also a unit of weight.
