# LedgerGuard architecture

LedgerGuard forecasts cash, matches financing options and analyses the books of a small business. The language model in it never acts directly. Its output is treated as data: a typed proposal that deterministic code checks, recomputes and gates before anything is written.

The judgement calls behind individual choices are in [DECISIONS.md](DECISIONS.md). Residual risks are in [RISK_REGISTER.md](RISK_REGISTER.md).

## 1. Two planes

```
┌──────────────────────────────────────────────────────────────┐
│  Web UI: console · cash-flow radar · approval inbox · audit  │
└───────────────────────────┬──────────────────────────────────┘
                            │ REST + server-sent events
┌───────────────────────────▼──────────────────────────────────┐
│  PROPOSER PLANE, probabilistic                               │
│  orchestrator → LLM adapter → tool-calling loop              │
│  output: ActionIntent, typed and never executed here         │
└───────────────────────────┬──────────────────────────────────┘
                            │ ActionIntent
┌───────────────────────────▼──────────────────────────────────┐
│  VERIFIER PLANE, deterministic                               │
│  1 schema · 2 recomputation · 3 business rules ·             │
│  4 provenance · 5 policy · 6 tier assignment                 │
│  output: VerifiedAction + Trust Receipt                      │
└───────────────────────────┬──────────────────────────────────┘
                            │ VerifiedAction
┌───────────────────────────▼──────────────────────────────────┐
│  EXECUTOR: T0/T1 run automatically · T2/T3 wait for approval │
└───────────────────────────┬──────────────────────────────────┘
                            │ the only writable connection
┌───────────────────────────▼──────────────────────────────────┐
│  Sandbox ledger in SQLite  +  hash-chained audit log         │
└──────────────────────────────────────────────────────────────┘
```

The proposer interprets the request, calls read-only tools and drafts one `ActionIntent`. It does not compute final figures, choose permission tiers or touch the ledger.

The verifier is plain Python. Given the same intent and the same ledger it always reaches the same verdict. It never reads the prompt, so text injected into the prompt cannot reach it.

The Executor holds the only writable database connection at runtime. It accepts only a `VerifiedAction` that the verifier sealed. An unverified intent is refused even if it has the right shape.

Correctness therefore does not depend on the model. A weak or manipulated model can propose poor plans or fail to produce a valid intent. It cannot make the system do something unsafe, because every figure is recomputed and every action is gated by code the model cannot influence.

## 2. The ActionIntent contract

`ActionIntent` is the model's only way to make anything happen. It is a strict Pydantic model. Unknown fields are rejected, types are never coerced and the action kind comes from a closed set.

| Field | Content |
|---|---|
| `kind` | One of `ANSWER_QUERY`, `DRAFT_FORECAST`, `RUN_SCENARIO`, `FLAG_ANOMALY`, `DRAFT_INVOICE`, `RECORD_PAYMENT`, `MARK_DISPUTED`, `RANK_FINANCING`, `INITIATE_PAYMENT`, `ACCEPT_FINANCING_OFFER` |
| `payload` | Fields specific to the kind |
| `claims` | Each factual assertion: a statement, an optional figure, an optional derivation naming a formula and its operand rows, and the source rows it rests on |
| `rationale` | A plain-language explanation for the owner |

A malformed intent is rejected and the user is told so. It is never repaired. Money must be a quoted decimal string, and a bare JSON number is refused because it has already passed through floating point. Payment payloads name invoices and nothing else. The verifier works out what is owed and seals that amount into the action, so the model has no field in which to name a sum.

The orchestrator retries a malformed intent at most twice, sending the parser's errors back unchanged, for every client alike. After that the turn fails with "the agent could not produce a valid action".

## 3. The six verification stages

| # | Stage | What it does | On failure |
|---|---|---|---|
| 1 | Schema | Parses the intent strictly | The intent is rejected and logged, and the user sees that the action was malformed |
| 2 | Recomputation | Re-derives every claimed figure from its cited rows | The recomputed value becomes the claim's figure. A difference raises `FIGURE_MISMATCH` and both values go on the receipt |
| 3 | Business rules | Runs each rule as a pure function of the intent and a ledger snapshot | A `BLOCK` is final. No tier or approval overrides it |
| 4 | Provenance | Checks that every claim cites existing rows of a kind that can support it | The claim is replaced by a visible marker saying the verifier removed it |
| 4b | Material omission | Checks that every fact the turn made mandatory was said | The fact is put back at the top of the answer and `MATERIAL_OMISSION` is recorded |
| 5 | Policy | Requires total cost of capital and a fixed disclosure on financing content, and removes guaranteed-outcome language | Financing without a verified total cost is blocked. Guarantee phrases are masked visibly |
| 6 | Tier assignment | Sets the tier from the action kind | A tier supplied by the model is discarded and recorded |

Numbers inside sentences get the same treatment as claimed figures. Every money-like number in the model's prose must equal a figure the verifier computed. A figure the verifier corrected is replaced by a marker that states the cited figure, and is never written into the model's sentence as if the model had said it. Any other unverified number is replaced by a visible marker. A wrong amount cannot survive inside a sentence. A figure or a mention in any currency other than the ledger's raises `CURRENCY_MISMATCH`. A matching amount is shown again in euros, and any other amount is masked.

### Facts and material omission

Tools give the model facts rather than raw rows. A fact has a stable id, a statement written by code, a figure and the derivation behind it. The model cites a fact by id and writes its sentence in its own words. The verifier does not trust a fact as the model echoes it. It recomputes the turn's facts from its own snapshot, resolves each cited id there and checks the figure.

Some facts are mandatory, because leaving them out would mislead the owner. A fact becomes mandatory when a declared condition holds:

* a projected week closes below zero within the 13 weeks;
* a rule blocks the action under discussion;
* a customer is above the concentration threshold and the answer concerns revenue;
* an alert is at severity high or above, unless it falls inside a shortfall that is already stated;
* a scenario was run, whose result is then stated;
* a financing answer is given, which always states the ranking with total costs and the do-nothing line.

A mandatory fact is always shown in the verifier's own sentence, word for word, and it comes first. The model's sentence about it is kept beside it as commentary. The model can add to the fact. It cannot replace it, soften it or turn a projection into a promise. A model that cites the shortfall and then says it should resolve itself passes a citation check, and code cannot reliably detect that kind of minimising language. Showing the fact in the verifier's words makes detecting it unnecessary.

If a response leaves a mandatory fact out, the verifier puts it in anyway and records `MATERIAL_OMISSION` on the receipt. A fact counts as said only when a surviving claim cites it. Mentioning it in passing is not enough.

Which facts a turn could cite, and which were mandatory, depends on the tools the orchestrator ran. The orchestrator records that list, and the model's output never sets it. Forecasts, scenarios and financing rankings are about cash whatever tools ran, so the shortfall is mandatory for them anyway. A block becomes citeable once the dry run described in section 10 has shown it to the model.

This stage is defence in depth. The tool layer is shaped so that an omission is unlikely: the material fact arrives as its own fact, marked mandatory. The verifier enforces it anyway, because unlikely is not never. In live runs the local model has led with the shortfall every time it was given facts, even with the mandatory marker hidden from it. So `MATERIAL_OMISSION` should fire rarely, and that is the intended result. A backstop that fired on most turns would mean the primary design had failed.

Most guardrails check whether what was said is true. This stage also checks whether what mattered was said, because an omission is how a financial assistant does real harm.

### Business rules

| Rule | Effect |
|---|---|
| `SufficientBalance` | Blocks a payment that would take the bank account below its floor |
| `AmountCeiling` | Blocks money leaving the business above the outbound ceiling |
| `DuplicatePayment` | Flags a payment of the same amount to the same supplier within 14 days. The action then always needs a person |
| `CounterpartyWhitelist` | Blocks money to a counterparty that is not whitelisted |
| `PayrollProtection` | Blocks a payment that would leave less projected cash than the next payroll needs, judged five days before pay day |
| `TaxReserveFloor` | Blocks a payment that would leave less cash than a tax payment due within 45 days |
| `DisputedInvoiceLock` | Blocks any settlement of a disputed invoice |
| `ConcentrationWarning` | Warns when a customer above 30% of revenue is involved. It never blocks |

The payroll and tax rules judge projected cash on contracted lines plus the order book. The speculative seasonal fill never justifies a payment.

## 4. Permission tiers

| Tier | Meaning | Examples | Gate |
|---|---|---|---|
| T0 | Read and analyse | query, forecast, anomaly flag | runs automatically |
| T1 | Draft or simulate, reversible, with no external effect | draft invoice, scenario, rank financing | runs automatically |
| T2 | Change internal records | record a receipt, mark an invoice disputed | an operator approves after seeing the exact ledger change |
| T3 | External or irreversible | pay a supplier, accept a financing offer | an operator approves, types a confirmation phrase, and waits out a 60-second cool-off |

The tier depends on the action kind alone. The outbound ceiling limits money leaving the business, and the only kinds that move money out are always T3, so the ceiling blocks rather than escalates. Recording a receipt has no ceiling. It cannot exceed what the invoice still owes, and that bound is checked again when it executes.

When a pending action is approved, every rule runs again on the ledger as it stands at that moment. Two payments that are each safe cannot be approved one after the other into a breach. A sealed action runs at most once.

Three invariants live in code so that configuration cannot weaken them. Payments and accepting a financing offer are always T3. The typed T3 confirmation cannot be turned off. A `BLOCK` cannot be overridden by any approval.

## 5. Trust Receipt

Every turn produces a receipt that the UI shows and that can be exported as JSON. It lists the rows read, each recomputation with the model's figure and the verifier's, every rule and its result, the provenance findings, the policy findings, every change made to the model's text, the risk flags, the tier, the approval state, the verifier version and the audit sequence number. Anyone can check an answer against the ledger without trusting the model.

## 6. Audit log

The audit log is append-only. Each event is chained to the one before it:

```
hash = sha256(prev_hash ‖ canonical_json(event))
```

`canonical_json` sorts keys, removes whitespace, writes decimals as strings and refuses floats. Database triggers refuse any UPDATE or DELETE on the log. `GET /api/audit/verify` walks the whole chain and reports the first event that does not match.

The log is tamper-evident with a published head hash. It is not tamper-proof. Someone with access to the database file could drop the triggers and rewrite an event together with every later hash. The chain would then be internally consistent, and only a head hash recorded somewhere else would reveal the change. The submission evidence publishes that head hash for this reason.

## 7. Write-path enforcement

The database connection enforces the rule that only the Executor writes. Convention does not.

Every component except the Executor gets a read-only session. SQLite is opened with `mode=ro` and every connection sets `PRAGMA query_only=ON`, so a write fails inside SQLite.

The Executor builds the only writable session the running app has. It accepts a `VerifiedAction` only if the HMAC seal the verifier computed over the whole action still matches. An action edited while it waits for approval is refused.

Provisioning also writes. Migrations and the seeder run from `scripts/seed.py` as a build step before the app starts. The app never imports that code, and a test starts the real app in a clean interpreter to prove it. This is the one documented exception to the Executor rule.

## 8. Data model

The ledger is SQLite through SQLAlchemy 2, with the schema managed by Alembic.

| Table | Holds |
|---|---|
| `business` | The one sandbox business, including `data_as_of`, the date the ledger treats as today |
| `account` | Bank, credit-line and cash accounts, with opening balances and the credit limit |
| `transaction` | Booked cash movements, each linked to the invoice or payroll run it settles |
| `counterparty` | Customers, suppliers, payroll, tax and others, with terms, risk band and whitelist flag |
| `invoice` | Receivables and payables with gross, tax, net, status, paid amount and description |
| `payroll_run` | Scheduled and paid payroll |
| `obligation` | Future tax, loan, lease and subscription payments, one row per occurrence |
| `financing_product` | A synthetic catalogue of financing products |
| `action_intent` | Every intent the proposer produced, with its status and, while pending, its sealed action |
| `trust_receipt` | The receipt for each intent |
| `audit_event` | The hash-chained log |

Every amount is a `Decimal` with two places, stored as integer cents. The money parser, the column type and canonical JSON all refuse floats. Rates are stored as exact text. The only currency is EUR.

All data comes from a deterministic seeder, and the same seed gives a byte-identical database on a given platform. D-054 explains why the Docker image's file differs in its bytes but not its data. The situations the demo relies on are listed in [fixtures/README.md](../fixtures/README.md).

## 9. Cash-flow projection

The projection covers 13 weeks and is simple enough to explain to an owner. Opening cash is the balance of the bank and cash accounts on the ledger date. Credit lines count as headroom, not cash. Each open receivable is expected on its due date plus that customer's average lateness in the past. Payables, payroll and obligations are expected on their due dates. Anything already past due lands in week 1. Disputed invoices are left out.

Every projected line carries a confidence class, and the classes are never merged into one number.

| Class | Meaning | Source |
|---|---|---|
| `CONTRACTED` | Amount and counterparty are fixed | Issued invoices, scheduled payroll, obligations |
| `EXPECTED` | Confirmed order book, not yet invoiced | Draft invoices |
| `SPECULATIVE` | Modelled only | After the order book ends, each counterparty is assumed to repeat what it invoiced in the same week last year. Each line cites that invoice |

Each week reports inflow and outflow per class and three running balances: contracted only, contracted plus expected, and all three classes.

Contracted-only cash is expected to go negative within 13 weeks. No small business holds a quarter's worth of signed orders, so cash backed only by signed commitments always runs out somewhere in the window. The band measures how much of the forecast rests on orders not yet signed. Alerts name the classes that drive them, and every shortfall is also quoted on contracted-only cash.

## 10. Local-first operation

The default model client talks to an open-weight model served by Ollama on the owner's machine. No invoice, transaction or payroll figure is sent to a model provider. That is possible because no figure or decision depends on the model being right.

| Client | Use |
|---|---|
| `LocalClient` | The default. Talks to local Ollama and passes the turn's JSON schema as `format`, so the runtime can only generate output in that shape |
| `OpenAICompatibleClient` | Optional, for any OpenAI-style endpoint. Nothing requires it |
| `ReplayClient` | Replays recorded responses. The tests, the demo and the Docker image use it with no model server and no network |

The schema is built for each turn from the action kinds the turn allows. A kind outside the allowlist cannot be expressed, and neither can an amount on a payment. Schema enforcement removes malformed output as a failure class. It does not make output correct, which is still the verifier's job.

### One turn

1. The plan, meaning the allowed action kinds and tools, comes from the owner's request alone, through a short list of readable patterns. A request that matches nothing can only be answered, not acted on.
2. The tools run on the read-only snapshot and return facts. Ledger text inside a fact is neutralised first, and anything stripped is written to the audit log.
3. The model drafts an ActionIntent under the turn's schema. A draft that fails the strict parser is retried at most twice, with the parser's errors sent back unchanged. After that the turn fails visibly with "The agent could not produce a valid action". Output is never repaired.
4. For actions that change records or move money, the rules run on the draft without changing anything. If a rule blocks, the model sees the block as a mandatory fact and writes its final answer once more, for the same kind of action. It may shrink the action, but it cannot turn a blocked payment into a chat answer. A scenario draft is run on a copy of the projection instead, and the model writes its final answer with the result in front of it.
5. The final output goes to the verifier with the list of tools that actually ran and the action kinds the plan allowed. A kind outside the plan is refused there as well as in the turn loop.

`POST /api/chat` streams each of these steps as a server-sent event, then the verified result with its receipt.

A turn takes about 15 seconds on the development machine, or about 35 seconds when a dry run adds a second call. The demo and the Docker image therefore replay recorded turns. A recording is matched by a fingerprint of the exact request. If the prompt, the facts or the schema change, the replay fails loudly instead of answering a different question with an old reply. Recordings are made against a fresh copy of the seeded ledger and are stored unedited.

## 11. Prompt-injection containment

Invoice text, counterparty names, transaction notes and the documents an owner attaches are written by outsiders, and five layers deal with them.

1. Outside text is fenced in data tags, after the owner's request and outside the instructions. An attached document arrives the same way. The owner attaches it but did not write it.
2. Instruction-shaped text is neutralised wherever code writes outside text into a sentence: facts, scenario results and rule reasons. What was removed is written to the audit log and shown to the owner as a containment notice. Because mandatory facts and block reasons are shown in the verifier's own words, this also keeps injected text out of sentences the system vouches for.
3. Each turn's allowed action kinds come from the owner's own words, never from retrieved text or attachments. They are enforced three times: by the grammar, by the turn loop and by the verifier.
4. Each turn is built fresh from the owner's words, the attachments and the ledger. Nothing from an earlier turn is carried into the next, so instructions planted across several turns have nothing to accumulate in.
5. Whatever the model proposes still passes the verifier, the rules and the tier gate, and none of them read the prompt. A payment can name only existing invoices, not a payee or an account.

The fifth layer holds even if the first four fail. The six adversarial fixtures and their findings are in `fixtures/README.md` and the security self-assessment.

## 12. The constraint layer is part of the system

Local models are run with a JSON schema that the runtime turns into a grammar, so the model cannot emit a token that breaks the schema. This is usually treated as safety with no downside. We found that the grammar also decides some of what the model says, and that from the outside its choices look exactly like a weak model choosing badly.

The first recording of scenario 3 asked what happens if Brennan Hospitality pays 30 days late. The model ran the contracted-receipts stress test instead. A prompt that described each scenario type changed nothing. Before concluding that a 7-billion-parameter model could not follow the request, we probed the runtime directly. Told outright to write a late-paying-customer scenario for `cp_brennan`, the model still produced the stress test. It could not do otherwise.

`scripts/probe_grammar.py` measures the cause. Each probe sends one short request with a schema at temperature zero and keeps the raw request and response. On Ollama 0.40.2 with the shipped model:

| Probe | Asked for | Came back |
|---|---|---|
| Three required properties declared as zebra, apple, mango | that order | apple, mango, zebra |
| One required property zed, optional beta and alpha | beta, alpha, zed | zed, beta. Alpha was dropped. |
| One branch per scenario type | a late-paying customer | the stress test |
| A flat scenario with a type and optional arguments, asked for invoice_id before days | a supplier invoice paid 14 days late | the invoice with an invented date and no delay |
| The same, asked for in the grammar's order | the same | exactly that |
| Every action kind under the turn grammar | each of the ten kinds in turn | each kind as asked, with its payload exact |

The grammar writes required properties first and optional ones after, each group in alphabetical order. Three consequences followed, and we met each of them in a real run.

1. With one branch per alternative, the ordering decides which branch the model can finish. A customer's fields sort before `type`, so a model that starts with `type` could only complete the stress-test branch, which has no other fields.
2. A flat object with a required type and optional arguments traps the model in a different way. Having written `days`, it can no longer write `counterparty_id`, which sorts first. Removing the branches did not remove the bug. The second recording of scenario 3 under the flat schema failed all three attempts, and each retry told the model to add a field that the grammar would not let it write.
3. The model copies the key order it is shown. An example in another order teaches it a sequence that the grammar cannot finish.

The fixes are structural. No generated grammar uses `anyOf`. A scenario is keyed by its type, and each type holds its own fields, all required, so there is nothing optional to skip past. Every example in the prompt is shown in the order its turn's grammar enforces. Two tests hold this for every grammar the proposer can produce: no grammar branches, and no object combines a required choice with two or more optional fields that depend on it. The top-level choice between action kinds was probed explicitly and was never biased. It is also flat now, with the payload of a turn that allows several kinds merged into one object of optional fields. D-041 explains why that one case is safe.

The verifier was built to police the model. This finding shows that it also polices the machinery around the model. A system that trusts its own constraint layer has moved the unverified step, not removed it. Here the strict parser rejected every trapped answer, the turn failed visibly, and nothing reached the ledger. Helpfulness suffered, safety did not. That is the claim of section 1 in a case we did not design.

The scenario example in the prompt was also moved off Brennan, because the demo's own answer should not sit in the prompt. Scenario 3 was recorded again after the move and still produced the Brennan scenario on its first attempt, so the result is not fed by the prompt.

## 13. Where the defences fire, measured

Section 3 describes recomputation as the stage that catches wrong figures, with text checking as a backstop for numbers written into prose. A sampling experiment, 50 answers to three figure-heavy requests at temperature 0.7, measured which stage actually fired. Recomputation found no mismatch in any of them. Every wrong figure was in the model's prose, five in all, and text checking masked each one.

Citing facts is what moved the errors. A claim that cites a fact carries the ledger's figure, so the structured fields are right before they are checked, and the model's arithmetic slips surface only in the sentences it writes around them. Text checking is therefore treated as a primary defence, with its own tests, not as decoration. `evidence/robustness/README.md` has the counts and the raw responses.

## 14. Development process

LedgerGuard was built with the help of an AI coding assistant. The architecture and the design decisions were made by the team, and the judgement calls behind them are recorded in DECISIONS.md. The git history is complete.

## 15. Implementation status

| Component | Status |
|---|---|
| Money, configuration, data model, migrations, seeder, projection with confidence classes | done |
| Ledger read API, Executor with sealed actions and tier gates, audit chain | done |
| The six verification stages, eight rules, tiers, Trust Receipt, approval queue with re-check | done |
| Facts with materiality, stage 4b with verbatim mandatory facts, currency check | done |
| Turn loop with retries and dry run, allowlist, schema-bound local client, replay and recording, chat stream | done |
| Cash-flow radar, financing matcher, financial analysis, alerts, scenarios, their API endpoints | done |
| Grammar probes, branch-free turn grammar, keyed scenarios | done |
| Six adversarial fixtures, all twelve scenarios recorded, evidence export, Docker image verified offline | done |
| Web UI: Console, Cash-Flow Radar, Approval Inbox, Audit | done |
