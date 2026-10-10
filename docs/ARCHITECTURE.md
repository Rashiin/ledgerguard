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

The local model client may retry a malformed intent twice, sending the schema error back unchanged. After that the turn fails with "the agent could not produce a valid action".

## 3. The six verification stages

| # | Stage | What it does | On failure |
|---|---|---|---|
| 1 | Schema | Parses the intent strictly | The intent is rejected and logged, and the user sees that the action was malformed |
| 2 | Recomputation | Re-derives every claimed figure from its cited rows | The recomputed value replaces the model's. A difference raises `FIGURE_MISMATCH` and both values go on the receipt |
| 3 | Business rules | Runs each rule as a pure function of the intent and a ledger snapshot | A `BLOCK` is final. No tier or approval overrides it |
| 4 | Provenance | Checks that every claim cites existing rows of a kind that can support it | The claim is replaced by a visible marker saying the verifier removed it |
| 5 | Policy | Requires total cost of capital and a fixed disclosure on financing content, and removes guaranteed-outcome language | Financing without a verified total cost is blocked. Guarantee phrases are masked visibly |
| 6 | Tier assignment | Sets the tier from the action kind | A tier supplied by the model is discarded and recorded |

Numbers inside sentences get the same treatment as claimed figures. Every money-like number in the model's prose must equal a figure the verifier computed. A figure the verifier corrected is substituted with the recomputed value. Any other number is replaced by a visible marker. A wrong amount cannot survive inside a sentence.

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

All data comes from a deterministic seeder, and the same seed gives a byte-identical database. The situations the demo relies on are listed in [fixtures/README.md](../fixtures/README.md).

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
| `LocalClient` | The default. Talks to local Ollama, asks for JSON output and retries a malformed intent at most twice |
| `OpenAICompatibleClient` | Optional, for any OpenAI-style endpoint. Nothing requires it |
| `ReplayClient` | Replays recorded responses. The tests, the demo and the Docker image use it with no model server and no network |

## 11. Prompt-injection containment

Invoice text, counterparty names, transaction descriptions and uploaded documents are untrusted, and four layers deal with them.

1. Untrusted text is wrapped in explicit data delimiters, outside the instruction part of the prompt.
2. Instruction-shaped patterns in untrusted text are neutralised before the prompt is built, and what was removed is logged.
3. Each turn's tool allowlist comes from the user's own request. Retrieved content cannot widen it.
4. Whatever the model proposes still passes the verifier and the tier gate, and neither reads the prompt.

The fourth layer holds even if the first three fail.

## 12. Development process

LedgerGuard was built with the help of an AI coding assistant. The architecture and the design decisions were made by the team, and the judgement calls behind them are recorded in DECISIONS.md. The git history is complete.

## 13. Implementation status

| Component | Status |
|---|---|
| Money, configuration, data model, migrations, seeder, projection with confidence classes | done |
| Ledger read API, Executor with sealed actions and tier gates, audit chain | done |
| The six verification stages, eight rules, tiers, Trust Receipt, approval queue with re-check | done |
| Proposer and model clients | planned |
| Cash-flow radar, financing matcher, financial analysis, alerts, scenarios | planned |
| Web UI | planned |
