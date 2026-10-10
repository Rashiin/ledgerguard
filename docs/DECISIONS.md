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
a writable engine.

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
  and confirmed purchase orders not yet invoiced.

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
