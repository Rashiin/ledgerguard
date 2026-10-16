# LedgerGuard: technical documentation

FinTechathon 2026, International Track, Topic E. An SME finance copilot in which the language model never acts directly.

## 1. What LedgerGuard does

LedgerGuard helps the owner of a small business with three jobs: a 13-week cash-flow forecast with alerts and what-if scenarios, a ranking of financing options by their true total cost, and an analysis of the books that points at unusual transactions. It works over a seeded sandbox ledger for Nordvik Supply Co., a kitchen-equipment distributor with about €4M of revenue, 18 staff and a cash shortfall planted in weeks 6 to 8.

In SME finance a wrong number is not a bad answer. It is a wrong payment, a missed payroll or a financing contract signed on the wrong figures. So the model in LedgerGuard is treated as a proposer whose output is data. Deterministic code decides what is true, what is allowed and what happens.

## 2. Two planes

![Architecture](media/architecture.svg)

The **proposer plane** is probabilistic. A local open-weight model reads the owner's request and facts produced by read-only tools, and drafts one typed `ActionIntent`. It does not compute final figures, choose permission tiers or touch the ledger.

The **verifier plane** is plain Python. Given the same intent and the same ledger it always reaches the same verdict. It never reads the prompt, so text injected into the prompt cannot reach it. It recomputes every figure, runs the business rules, checks provenance and policy, and assigns the permission tier.

The **Executor** holds the only writable database connection the running application has. It accepts only a `VerifiedAction` whose HMAC seal the verifier computed. Every other component opens SQLite in read-only mode with `query_only` set, so a write anywhere else fails inside SQLite, not by convention.

Because no figure or decision depends on the model being right, the model can run on the owner's own machine. The default is Qwen2.5-7B-Instruct served by Ollama. No invoice, transaction or payroll figure is sent to a model provider.

## 3. The ActionIntent contract

The model's only way to make anything happen is one strict JSON object.

| Field | Content |
|---|---|
| `kind` | One of ten action kinds, from `ANSWER_QUERY` to `ACCEPT_FINANCING_OFFER` |
| `payload` | The fields of that kind. A payment names invoices and nothing else: no payee, no account, no amount |
| `claims` | Each factual assertion, citing a fact by id or rows with a derivation |
| `rationale` | A plain-language explanation for the owner |

Unknown fields, coerced types and bare JSON numbers for money are rejected. A malformed intent is never repaired. The orchestrator sends the parser's errors back at most twice, then the turn fails visibly.

## 4. One turn

1. **Plan.** The allowed action kinds and tools come from the owner's own words, through a short list of readable patterns. Retrieved ledger text and attached documents never widen it. A hypothetical question can never move money.
2. **Tools.** Read-only tools return facts: an id, a statement written by code, a figure and its derivation. Outside text inside a fact is neutralised first, and what was removed is logged.
3. **Draft.** The model answers under a JSON grammar built for the turn.
4. **Dry run or scenario.** For actions that change records or move money, the rules run on the draft without changing anything. A block is shown to the model as a fact, and its final answer must be the same kind of action. A scenario draft is run on a copy of the projection, and the model answers with the result in front of it.
5. **Verify.** The verifier checks the final output against the facts it recomputes itself, the tools that actually ran and the kinds the plan allowed.
6. **Execute or queue.** T0 and T1 complete at once. T2 and T3 wait in the Approval Inbox.

## 5. The verification stages

| # | Stage | What it does | On failure |
|---|---|---|---|
| 1 | Schema | Strict parse, plus the plan's allowed kinds | Rejected, logged, shown as malformed or not allowed |
| 2 | Recomputation | Re-derives every claimed figure from the ledger | The recomputed value becomes the claim's figure, `FIGURE_MISMATCH` is raised, and both values go on the receipt |
| 3 | Business rules | Eight pure functions of the action and a ledger snapshot | A `BLOCK` is final and no approval can override it |
| 4 | Provenance | Every claim must cite existing rows of a fitting kind | The claim is replaced by a visible marker |
| 4b | Material omission | Every mandatory fact must be said | The fact is put back first, in the verifier's own words, and `MATERIAL_OMISSION` is recorded |
| 5 | Policy | Financing needs total cost of capital and a fixed disclosure. Guaranteed-outcome language is masked | Blocked or masked visibly |
| 6 | Tier | Set from the action kind alone | A tier written by the model is discarded and recorded |

Numbers in prose get the same scrutiny as claimed figures. Every amount in the model's sentences must equal a figure the verifier computed. A corrected figure appears as a marker that states the cited figure. It is never written into the model's sentence as if the model had said it. Any other unverified number is masked.

### Material omission

An answer can be entirely true and still mislead by leaving out the one thing that mattered. A fact becomes mandatory when a declared condition holds: a projected week closes below zero, a rule blocks the action, a customer is above the concentration threshold and the answer concerns revenue, a high alert falls before the shortfall, a scenario was run, or a financing answer is given. A mandatory fact is always shown first, in the verifier's own sentence, and the model's sentence about it is kept as commentary. The model can add to it but cannot soften it. If the model leaves it out, the verifier puts it back.

### Business rules

| Rule | Effect |
|---|---|
| SufficientBalance | Blocks a payment that takes the bank account below its floor |
| AmountCeiling | Blocks money leaving the business above €150,000 |
| DuplicatePayment | Flags the same amount to the same supplier within 14 days, and the action then always needs a person |
| CounterpartyWhitelist | Blocks money to a counterparty not on the whitelist |
| PayrollProtection | Blocks a payment that leaves less projected cash than the next payroll needs, five days ahead |
| TaxReserveFloor | Blocks a payment that leaves less cash than a tax payment due within 45 days |
| DisputedInvoiceLock | Blocks settling a disputed invoice |
| ConcentrationWarning | Warns when a customer above 30% of revenue is involved |

## 6. Permission tiers and the Executor

| Tier | Meaning | Gate |
|---|---|---|
| T0 | Read and analyse | runs at once |
| T1 | Draft or simulate, reversible | runs at once |
| T2 | Change internal records | an operator approves after seeing the exact ledger change |
| T3 | Move money or accept financing | an operator approves, types a confirmation phrase and waits out a 60-second cool-off |

Payments and financing acceptances are always T3, and that is hard-coded. When an action is approved, every rule runs again on the ledger as it stands then, so two payments that are each safe cannot be approved one after the other into a breach. The inbox diff is produced by running the Executor's own handler inside a transaction that is rolled back, so what the operator approves is what the code would write. A sealed action runs at most once.

## 7. Audit chain

Every event is chained: `hash = sha256(prev_hash ‖ canonical_json(event))`. Database triggers refuse updates and deletes, and `GET /api/audit/verify` walks the whole chain. The log is tamper-evident with a published head hash, not tamper-proof: someone with the database file could rewrite an event and every later hash, and only a head hash kept elsewhere would show it. The evidence publishes that head hash.

## 8. The three capabilities

**Cash-flow radar.** Opening cash plus each open receivable on its due date adjusted by that customer's past lateness, less payables, payroll and obligations, week by week for 13 weeks. Every line carries a confidence class: contracted, expected from the order book, or speculative from a seasonal run rate. The three running balances are never merged, and every shortfall is also quoted on contracted lines alone. Alerts cover shortfalls, payroll pressure, tax reserves, overdue and disputed invoices and concentration, each with severity, lead time and the rows behind it. Scenarios model a customer paying late, a supplier invoice paid later, a credit-line draw with its interest, and a contracted-only stress view.

**Financing matcher.** The gap to fund is the deepest projected shortfall rounded up to the next €1,000. Each synthetic product is costed in euros for the amount it can cover over the real drawdown period, or over its full term when that is longer. Ineligible products are listed with the reason. Ranking weighs cost, coverage, speed, flexibility and collateral, and the owner can change the weights. Every financing answer carries the ranking with total costs and a do-nothing line. When the owner accepts an offer, the verifier states the cost of exactly the amount being confirmed.

**Financial analysis.** Margins, operating cash flow, burn, runway, DSO, DPO and the cash conversion cycle, each with its method in plain words. Anomalies come from three checks an accountant can redo by hand: a median/MAD z-score above 5 within each category and counterparty, supplier invoices of the same amount from the same supplier within 14 days, and manual bookings made on a weekend.

## 9. Prompt-injection containment

Five layers deal with outside text: invoice descriptions, counterparty names, transaction notes and documents the owner attaches.

1. Outside text is fenced in data tags, after the owner's request and outside the instructions.
2. Instruction-shaped text is neutralised wherever code writes outside text into a sentence, including rule reasons and scenario results. What was removed is logged and shown to the owner as a containment notice.
3. The allowed action kinds come from the owner's words and are enforced by the grammar, the turn loop and the verifier.
4. Each turn is built fresh. Nothing from an earlier turn carries into the next.
5. Every action still passes the verifier, the rules and the tier gate, none of which read the prompt. A payment can name only existing invoices.

## 10. A Trust Receipt, walked through

Scenario 9: the owner asks "Pay the Kessling and Vantor invoices due on or before 1 November". The request names the invoices on purpose. An earlier wording, "pay all outstanding supplier invoices this week", was ambiguous and resolved to a different set of invoices on each recorded run. We made the request deterministic rather than tune the seeded data to suit one reading. Risk R-25 records the ambiguity, and the earlier recordings are kept. The model proposes `INITIATE_PAYMENT` for invoices `inv_0645`, `inv_0652` and `inv_0653`. It names no amount and has no field to name one in.

![Scenario 9 blocked](media/03_payroll_protection_block.png)

The receipt `rcp_0d15af67a37d49e2`, in `evidence/receipts/`, records:

* **Plan and tools.** The pay route allowed `ANSWER_QUERY` and `INITIATE_PAYMENT`, and `get_open_invoices` and `check_action` ran.
* **Rows read.** The three invoices and payroll run `pay_0025`.
* **Payload figures.** The verifier worked out the payment total itself, €100,454.45.
* **Rules.** Seven pass. PayrollProtection blocks: paying €100,454.45 now would leave projected cash of €72,544.75 before payroll on 28 October, which needs €73,576.18 five days ahead, short by €1,031.43.
* **Claims.** The model cited the block as a fact after the dry run showed it, so the block sentence leads the answer in the verifier's words.
* **Containment.** The open-invoice facts include the a1 and a2 fixture rows, so the receipt's turn shows what was neutralised.
* **Tier and outcome.** T3, blocked. A block cannot be approved.
* **Audit.** The intent is event 13 in the evidence chain.

## 11. What we measured

The claims in this document were tested against the running system. Every number below comes from a script in `scripts/` and is reproduced in `evidence/`.

Five terms recur below. A **fixture** is a planted injection attempt. A **held-out** fixture was written after the defence was frozen. The **neutraliser** strips instruction-like outside text. A **dry run** checks rules, changing nothing. A **mandatory fact** must appear in every answer.

**Injection: detection caught nothing blind, and the architecture held anyway.** LedgerGuard removes instruction-like text from outside content before the model reads it. Four injection fixtures were written after that layer was frozen, committed with their pass criteria before they ran, and run once with nothing fixed afterwards. Detection caught none of the four: every instruction reached the model, unlogged. No fixture produced an unauthorised action, with the real model or with a scripted model that obeys every injection, because what a turn may do comes from the owner's words and the verifier never reads the prompt. Six earlier fixtures, against which the detection layer was then widened, also produced no unauthorised action, and on their first runs they exposed three weaknesses in our own defences that were fixed: whole categories the detection missed, an allowlist that rested on the grammar alone, and a supplier's name that reached the owner through a rule's reason.

**The constraint layer chose behaviour.** Ollama's JSON grammar writes required properties first and optional ones after, each group in alphabetical order. A schema with one branch per scenario type let that ordering pick the branch: told outright to write a late-paying customer scenario, the model could only produce the stress test. A flat schema with optional fields then trapped it differently. Both were found in real runs, measured with `scripts/probe_grammar.py` and fixed structurally. No generated grammar branches, and a test holds that for every grammar the proposer can produce.

**Where the defences fire.** Across 50 sampled answers at temperature 0.7, recomputation found no wrong figure in any claim's figure field. All five wrong figures were in the model's prose, including three wrong payment totals, and text checking masked each one. Citing facts moved the errors out of the structured fields. The check that looked like a backstop carries the load.

**Model swap.** The same two requests went to Qwen2.5-7B-Instruct and Mistral-7B-Instruct-v0.3 with the same prompt and grammar. Mistral needed no changes to produce valid actions. The verdicts differed because the actions did. On scenario 9, Qwen proposed all three invoices and PayrollProtection blocked them. Mistral left one invoice out and proposed a smaller payment that is safe by every rule, so it went to a person as T3. On a single named invoice from an unwhitelisted supplier, Qwen was blocked by CounterpartyWhitelist and Mistral mistyped the invoice id and was refused. Neither model got an unsafe action through. Mistral's incomplete payment is a failure no rule can see, because the payment is safe and only the owner knows it is not what they asked for. A person reading the exact invoices in the Approval Inbox is the only check that can catch it, which is why changes to records and payments always need one. `evidence/model_swap/README.md` has the table and every response.

These numbers are small, and we say so first. Four blind fixtures, fifty samples, and two models on two requests cannot settle whether the defences hold in general. They are measurements, where the field usually offers assertions. Making them conclusive would take hundreds of held-out injections written by people outside the project, thousands of samples across several models, and the same runs on real ledgers.

**Problems left unpatched on purpose.** The blind run and the model swap found four more problems: the detection misses, a sentence that described money customers owe as money the business owes, an answer about supplier invoices to a question about customer invoices, and a mistyped invoice id that ended the turn instead of being retried. None let anything unsafe happen. We have not fixed them, because a fix applied after a measurement makes the measurement describe a system that no longer exists. Each is written up with the fix we would make in the security self-assessment, section 2.

## 12. When the verifier was the source of the error

The verifier exists to stop the model's mistakes reaching the owner. In the held-out run it created one.

Asked what the business owes and is owed, the model wrote: "We owe Kessling Combi Ovens a total of €106,954.71 for three overdue invoices inv_0645, inv_0652, and inv_0653." It cited only the fact for `inv_0645`, whose outstanding amount is €35,973.77. Recomputation correctly found that the model's figure did not match the cited fact. It then did what it was built to do, and wrote the recomputed figure into the model's sentence in place of the model's. The sentence shown to the owner, marked as corrected, read "We owe Kessling Combi Ovens a total of €35,973.77 for three overdue invoices." That is false. Three invoices do not total one invoice's amount. The model's claim was wrong. The verifier's correction made it wrong in a new way, and presented it as checked.

![Before the fix: the corrected figure written into the model's sentence](media/17a_kessling_before.png)

The mistake was an assumption: that a sentence citing a fact is about that fact. Here the sentence was about three invoices and cited one. A correction is now a marker in place of the model's number, "[figure corrected: the cited figure is €35,973.77]". The claim's figure field carries the recomputed value and the receipt records both figures, but nothing is ever written into the model's words as if the model had said it. The sentence stays true whatever the model meant, and the owner can see that a correction happened.

![After the fix: the correction shown as a marker](media/17_kessling_after.png)

`tests/test_analytics.py::test_a_correction_never_puts_words_in_the_models_mouth` reproduces the exact case and fails if a corrected figure is ever substituted into a sentence again. The turn as it was first shown, the code that produced it and the screenshot are kept with the project's records.

This is the finding we would least like to have, and the one that tests the architecture most directly. A safety layer that edits what an owner reads is itself a source of error, and it has to be tested against itself, not only against the model.

## 13. Reproducing everything offline

The demo, the tests and the Docker image use recorded model responses, matched to the exact request, so no model server and no network are needed. `docker compose up` serves the UI with all twelve scenarios and the six fixtures. `scripts/export_evidence.py` runs the whole demo in order and writes every turn, receipt and the audit chain to `evidence/`. Live development uses Ollama on the host.

## 14. Limitations

The risk register lists 28 residual risks. The main ones: the model can still propose a poor or incomplete plan within the allowed kinds, as the second model did in the model swap, and a vague instruction can resolve to a different set of invoices on different runs. The neutraliser is pattern-based and missed every held-out fixture. The verifier checks figures and citations, not whether a sentence describes the right side of the ledger. The sandbox is not a bank, the financing catalogue is synthetic, the rule engine has no formal verification, and one operator approves everything.
