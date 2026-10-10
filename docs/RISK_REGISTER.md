# Known-risk register

Residual risks, recorded as they are identified. This feeds the security
self-assessment. Each entry: the risk, what limits it today, and what is left.

| ID | Risk | Mitigation in place | Residual |
|---|---|---|---|
| R-01 | **A write path outside the Executor.** Provisioning (migrations and the seeder) writes the database. | Runs offline as a build step (`scripts/seed.py`) before the app starts. A test starts the real app in a clean interpreter and asserts that no provisioning, seeder or Alembic module is ever loaded. Another test asserts that engines are created only in four named files. | Anyone who can run the build scripts can rewrite the ledger. This is a documented exception to "only the Executor writes", not a runtime path. |
| R-02 | **The sealing key is readable by code in the application process.** | The seal stops accidental bypass, hand-built actions and edits to stored pending actions. The key file is mode 0600 and never committed. | A compromised application process could forge seals. Out of scope for a single-machine sandbox. |
| R-03 | **Someone with file access rewrites the audit log consistently** (an event and every later hash). | Triggers block UPDATE and DELETE. The hash chain detects any edit that leaves later hashes unchanged. The head hash is published with the submission evidence. | The chain is unkeyed SHA-256, so a full consistent rewrite needs file access only, no secret. It is detectable only by comparing against a head hash recorded elsewhere. The log is **tamper-evident with a published head hash**, not tamper-proof. |
| R-04 | **The projection's seasonal fill is crude.** | It is tagged `SPECULATIVE`, never merged with contracted or expected cash. Each line cites the invoice it repeats. The shortfall is also reported on contracted-only cash. | No growth or trend adjustment. Last year's one-off orders are repeated. |
| R-05 | **Variable spend is not projected** (fuel, utilities, insurance, bank fees: about €4k a month). | Documented in fixtures/README.md and DECISIONS D-005. | The projection is slightly optimistic by about that amount. |
| R-06 | **Single-operator trust model.** | T2 and T3 need a human approver role, and T3 adds a typed confirmation and a cool-off. | There is no authentication or separation between "operator" and "owner". One person at the keyboard can approve anything that is not blocked. |
| R-07 | **Sandbox only.** | No bank or payment-provider integration exists. | Execution means a ledger row change, not money movement. |
