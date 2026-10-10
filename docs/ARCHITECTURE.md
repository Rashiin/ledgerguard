# LedgerGuard architecture

LedgerGuard is a finance copilot for small and medium businesses. It
forecasts cash, matches financing options and analyses the books. Its central
design rule is that **the language model never acts directly**. The model's
output is treated as data: a typed proposal that deterministic code checks,
recomputes and gates before anything is written or executed.

This document describes the design. The reasons behind individual choices are
logged in [DECISIONS.md](DECISIONS.md).

---

## 1. Two planes

```
┌──────────────────────────────────────────────────────────────┐
│  Web UI: console · cash-flow radar · approval inbox · audit  │
└───────────────────────────┬──────────────────────────────────┘
                            │ REST + server-sent events
┌───────────────────────────▼──────────────────────────────────┐
│  PROPOSER PLANE (probabilistic)                              │
│  orchestrator → LLM adapter → tool-calling loop              │
│  output: ActionIntent  (typed, never executed here)          │
└───────────────────────────┬──────────────────────────────────┘
                            │ ActionIntent
┌───────────────────────────▼──────────────────────────────────┐
│  VERIFIER PLANE (deterministic)                              │
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
│  Sandbox ledger (SQLite)  +  hash-chained audit log          │
└──────────────────────────────────────────────────────────────┘
```

**Proposer plane.** The model interprets the request, plans, calls read-only
tools, and drafts one `ActionIntent`. It does not compute final figures,
decide permission tiers or touch the ledger.

**Verifier plane.** Plain, deterministic Python. Given the same intent and
the same ledger, it always reaches the same verdict. It does not read the
prompt, so text injected into the prompt cannot reach it.

**Executor.** The only component that holds a writable database connection.
It accepts only a `VerifiedAction` produced by the verifier. Anything else is
refused, including an unverified intent that has the right shape.

The consequence: **correctness does not depend on the model.** A weaker or
manipulated model can make the copilot less helpful, by proposing poor plans
or failing to produce a valid intent. It cannot make it unsafe, because every
figure is recomputed and every action is gated by code the model cannot
influence.

---

## 2. The ActionIntent contract

`ActionIntent` is the model's only output channel for anything
consequential. It is a strict Pydantic model: unknown fields are rejected,
types are not coerced, and the action kind is a closed enumeration.

| Field | Content |
|---|---|
| `kind` | One of `ANSWER_QUERY`, `DRAFT_FORECAST`, `RUN_SCENARIO`, `FLAG_ANOMALY`, `DRAFT_INVOICE`, `RECORD_PAYMENT`, `MARK_DISPUTED`, `RANK_FINANCING`, `INITIATE_PAYMENT`, `ACCEPT_FINANCING_OFFER` |
| `payload` | Kind-specific, as a discriminated union |
| `claims` | Every factual assertion, each with a statement, an optional figure, an optional derivation (a formula over operand row ids) and a non-empty list of source row ids |
| `rationale` | Natural-language explanation for the human |

A malformed intent is rejected and the rejection is shown to the user. It is
never repaired. Money must be a quoted decimal string; a bare JSON number is
refused. Payment payloads name invoices only: the verifier computes what is
owed and seals it into the action, so the model has no field through which
to name a sum. The local model client may retry at most twice, feeding the
schema error back verbatim. After that, the turn fails visibly with "the
agent could not produce a valid action".

---

## 3. The six verification stages

| # | Stage | What it does | On failure |
|---|---|---|---|
| 1 | **Schema** | Strict parse of the intent | Rejected and logged; the user sees "malformed action" |
| 2 | **Recomputation** | Every claimed figure is re-derived from its cited rows in plain Python | The recomputed value replaces the model's; any difference raises `FIGURE_MISMATCH`. Both values go on the receipt |
| 3 | **Business rules** | A registry of pure functions `(intent, ledger snapshot) → PASS / WARN / BLOCK` with a reason | `BLOCK` is absolute: no tier or approval overrides it |
| 4 | **Provenance** | Every claim must cite existing rows of a type that can support it | Unsupported claims are replaced by a visible "unsupported, removed by verifier" marker, never deleted silently |
| 5 | **Policy** | Financing output must show total cost of capital over the real term and carry a non-removable "informational, not regulated advice" disclosure. Guaranteed-outcome language is refused | Rejected or rewritten with the disclosure attached |
| 6 | **Tier assignment** | A deterministic function of action kind and amount | Any tier the model supplies is ignored |

**Numbers in prose.** Every money-like number in the model's sentences must
equal a figure the verifier computed. A number the verifier corrected is
replaced with the recomputed value; anything else is masked visibly. A wrong
amount cannot survive inside a sentence.

### Business rules

| Rule | Effect |
|---|---|
| `SufficientBalance` | Blocks outbound actions that take an account below its configured floor |
| `AmountCeiling` | Escalates an amount above its tier's ceiling by one tier; blocks above the highest ceiling |
| `DuplicatePayment` | Flags a payment to the same counterparty for the same amount within 14 days; never auto-passes |
| `CounterpartyWhitelist` | Outbound money only to whitelisted counterparties |
| `PayrollProtection` | Blocks actions that would leave the projected balance below the next payroll run's requirement within its lead time |
| `TaxReserveFloor` | Ring-fences upcoming tax obligations |
| `DisputedInvoiceLock` | No settlement action on a disputed invoice |
| `ConcentrationWarning` | Advisory only; never blocks |

---

## 4. Permission tiers

| Tier | Meaning | Examples | Gate |
|---|---|---|---|
| **T0** | Read and analyse | query, forecast, anomaly flag | automatic |
| **T1** | Draft or simulate: reversible, no external effect | draft invoice, scenario, rank financing | automatic |
| **T2** | Change internal records | record a payment received, mark disputed | operator approval, with a before/after diff of the ledger change |
| **T3** | External or irreversible | initiate payment, accept a financing offer | operator approval, plus a typed second confirmation, an amount ceiling and a 60-second cool-off |

The payroll and tax rules judge projected cash on contracted plus order-book
lines. The speculative fill never justifies a payment.

**Approval re-check.** When a pending action is approved, every rule runs
again on the ledger as it is now. Two payments that are safe alone cannot be
approved one after the other into a breach. Each sealed action runs at most
once.

Ceilings, the cool-off and rule parameters live in `config/policy.yaml`, which
is validated strictly when the application starts. Three invariants are
hard-coded and cannot be configured away:

* `INITIATE_PAYMENT` and `ACCEPT_FINANCING_OFFER` are always T3;
* the typed T3 confirmation cannot be disabled;
* a `BLOCK` cannot be overridden by any approval.

---

## 5. Trust Receipt

Every agent turn produces a Trust Receipt, rendered in the UI and exportable
as JSON. It records:

* which rows were read;
* each recomputation (the model's figure, the recomputed figure, the formula
  and its operands);
* every rule evaluated and its result;
* the risk flags;
* the tier and the approval state;
* the verifier version;
* the audit-log sequence number.

A reader can check any answer against the ledger without trusting the model.

---

## 6. Audit log

The audit log is append-only. Each event stores

```
hash = sha256(prev_hash ‖ canonical_json(event))
```

where `canonical_json` sorts keys, strips whitespace, writes decimals as
strings and refuses floats. The database refuses UPDATE and DELETE on the log
through triggers. The hash chain detects tampering by anyone who removes
them. `GET /api/audit/verify` walks the whole chain and
reports whether it is intact. Changing, deleting or reordering any event
breaks every hash after it. The full chain can be exported as execution
evidence.

The log is **tamper-evident with a published head hash**, not tamper-proof.
Someone with access to the database file could rewrite an event and every
later hash consistently. That is detectable only against a head hash recorded
elsewhere, which is why the exported evidence publishes it.

---

## 7. Write-path enforcement

The rule "only the Executor writes" is enforced by the database connection,
not by convention:

* Every component except the Executor receives a **read-only** session. SQLite
  is opened with `mode=ro`, and each connection sets `PRAGMA query_only=ON`.
  A write through it fails inside SQLite.
* The Executor builds the only runtime **writable** session. It only accepts a
  `VerifiedAction` whose HMAC seal, computed by the verifier over the whole
  action, still matches. An action edited after verification (for example
  while it waits for approval) is refused.
* **Provisioning** (migrations and the synthetic-data seeder) also writes. It
  runs as an offline build step before the application starts, from
  `scripts/seed.py`. The application never imports it, and the test suite
  asserts that. This is the single documented exception to the Executor rule.

---

## 8. Data model

SQLite through SQLAlchemy 2, with schema managed by Alembic.

| Table | Purpose |
|---|---|
| `business` | The one sandbox business, including `data_as_of`, the ledger's "today" |
| `account` | Bank, credit-line and cash accounts (opening balance, credit limit) |
| `transaction` | Booked cash movements, linked to the invoice or payroll run they settle |
| `counterparty` | Customers, suppliers, payroll, tax authority and others, with terms, risk band and whitelist flag |
| `invoice` | Receivables and payables: gross, tax, net, status, paid amount, free-text description |
| `payroll_run` | Scheduled and paid payroll |
| `obligation` | Future tax, loan, lease and subscription payments, one row per occurrence |
| `financing_product` | A synthetic catalogue of financing products |
| `action_intent` | Every intent the proposer produced, with its status |
| `trust_receipt` | The receipt for each intent |
| `audit_event` | The hash-chained log |

### Money

* Every amount is a `Decimal` with two places, stored as integer cents.
* Floats are refused at every entry point: the money parser, the column type
  and canonical JSON.
* Non-money decimals such as rates are stored as exact text.
* The single currency is EUR.

### Synthetic data

All data is generated by a deterministic seeder: the same seed gives a
byte-identical database. The planted situations the demo relies on are listed
in [fixtures/README.md](../fixtures/README.md).

---

## 9. Cash-flow projection

A 13-week weekly projection, built to be explained to a business owner.

* **Opening:** cash on bank and cash accounts at the as-of date. Credit lines
  are financing headroom, not cash.
* **Receivables:** each open receivable is expected on its due date plus that
  customer's historical mean lateness.
* **Outflows:** payables, payroll and obligations on their due dates.
* **Already past due:** items past due land in week 1.
* **Disputed invoices:** excluded.

### Confidence classes

Every projected line carries one class. The classes are reported separately
and never merged into one number.

| Class | Meaning | Source |
|---|---|---|
| `CONTRACTED` | Amount and counterparty fixed | Issued invoices, scheduled payroll, obligations |
| `EXPECTED` | Confirmed order book, not yet invoiced | Draft invoices |
| `SPECULATIVE` | Modelled only | Seasonal fill after the order book: each counterparty repeats what it invoiced in the same week last year, citing that invoice |

Each week reports inflow and outflow per class, and three nested closing
balances: contracted only, contracted plus expected, and all classes.

**Contracted-only cash is expected to go negative within 13 weeks.** No small
business holds a quarter of signed orders, so contracted-only cash always
runs out somewhere in the window. That is not a data error or a failing
business: it shows how much of the forecast rests on orders not yet signed. Alerts
name the classes that drive them, and a shortfall is always also quoted on
contracted-only cash.

---

## 10. Local-first operation

The default model client talks to a local open-weight model served by Ollama
on the owner's own machine. No ledger data (invoices, transactions, payroll)
is sent to a third-party model provider. This is possible because no figure
or decision depends on the model being right.

| Client | Use |
|---|---|
| `LocalClient` | Default. Local Ollama server, JSON-constrained output, at most two schema-error retries |
| `OpenAICompatibleClient` | Optional, for an OpenAI-shaped endpoint. Never required |
| `ReplayClient` | Replays recorded model responses. The tests, the demo and the Docker image run on it with no model server and no network |

---

## 11. Prompt-injection containment

Invoice text, counterparty names, transaction descriptions and uploaded
documents are untrusted. The defences are layered:

1. **Delimiting.** Untrusted spans are wrapped in explicit data delimiters,
   outside the instruction region of the prompt.
2. **Neutralising.** Instruction-shaped patterns in untrusted spans are
   neutralised before they enter the prompt, and what was removed is logged.
3. **Tool allowlist.** Each turn's allowlist is derived from the user's own
   request. Retrieved content cannot widen it.
4. **Deterministic gates.** Whatever the model proposes still passes the
   verifier and the tier gate, which never read the prompt.

The fourth layer does not depend on the first three working.

---

## 12. Implementation status

| Component | Status |
|---|---|
| Money, configuration, data model, migrations, seeder, projection with confidence classes | done |
| Ledger read API, Executor (sealed actions, tier gates), audit chain with tamper detection | done |
| Verifier plane (six stages), eight rules, tiers, Trust Receipt, approval queue with re-check | done |
| Proposer plane and LLM clients | planned |
| Capabilities (radar, financing matcher, analysis), alerts, scenarios | planned |
| Web UI | planned |
