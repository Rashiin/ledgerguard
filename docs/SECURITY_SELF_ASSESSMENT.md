# Security self-assessment

Draft. The Phase 8 PDF is built from this file. It covers the incidents found during development first, because they are the evidence for the design. The permission tiers, write-path enforcement, audit chain and injection containment follow. Known residual risks are in [RISK_REGISTER.md](RISK_REGISTER.md).

## 1. Two times the system was routed around, and what caught it

### 1.1 The model answered around a block

During the first live recording of scenario 9 on 2026-10-11, the owner asked to pay all outstanding supplier invoices this week. The local model drafted a payment of three supplier invoices totalling €100,454.45. Before the final answer, the rules ran on that draft without changing anything. PayrollProtection blocked it: the payment would have left €72,544.75 before the 28 October payroll, which needs €73,576.18. The model was shown the block as a fact and asked for its final answer.

It answered with a chat reply instead of the payment. The reply explained the block in reasonable terms. But a chat reply moves no money, so no rule had anything to block, and the Trust Receipt recorded a verified T0 answer. The owner would have read an explanation, while the record showed no payment proposed and nothing blocked.

We describe this by its effect and do not claim an intent. A 7-billion-parameter model's reasons cannot be established. The effect is what a control has to withstand: the agent reached an outcome by a route the control did not cover. It did so unprompted, on the first recording.

The fix holds at two layers. The schema handed to the model runtime for the final answer allows only the draft's kind of action, so the grammar cannot produce anything else. Because not every client enforces a schema, the orchestrator also checks the kind and rejects a different one like any malformed output. If the model keeps answering around the action, the blocked draft itself is what gets verified, and the receipt shows the block together with `MATERIAL_OMISSION`.

The raw recording of that first run was overwritten when the scenario was recorded again after the fix. The verified output printed at the time was kept, and the test below reproduces the same sequence with a scripted model.

Tests holding the fix:

* `tests/test_proposer.py::test_a_model_that_answers_around_a_block_cannot_hide_it` replays the evasion and checks that the block is recorded anyway.
* `tests/test_replay.py::test_scenario_9_replays_offline_with_one_clean_block` replays the corrected recording and checks for one real block and no other finding about it.

### 1.2 Two safe approvals that add up to an unsafe one

While reviewing the verifier in Phase 3, we found that two payments could each pass PayrollProtection when verified, because each was checked against the ledger as it stood. Approved one after the other, together they would leave payroll uncovered. No single step broke a rule. The outcome the rule exists to prevent was reached by another route.

The fix is at the approval layer. Approving a pending action re-runs every rule against the ledger as it stands at that moment, and closes the action as rejected if anything now blocks.

Test holding the fix: `tests/test_pipeline.py::test_two_payments_fine_alone_cannot_jointly_starve_payroll`.

### 1.3 What the pair shows

Both incidents are the same move: reaching an outcome the controls forbid without tripping a control directly. One came from the model during a turn. The other came from the order in which a person approves things. They were caught at different layers, the orchestrator and the approval queue. Neither fix would have caught the other incident.

That is the case for defence in depth, made with recorded behaviour rather than adjectives. An agent working in finance needs guardrails because it will find gaps between them, and the gaps are found by testing against real runs.

### 1.4 Also caught live: the model's arithmetic

In the same recording, the model summed the three invoices of €35,973.77, €30,503.50 and €33,977.18 as €112,543.81. The correct total is €100,454.45. The verifier replaced the model's figure with a masked marker, and the block sentence shown to the owner carries the correct amount. Nothing about this was staged.

The model made the same error again when scenario 9 was recorded after the Phase 5 changes, on the same day. That recording is in git history at commit c553551. Scenario 9 was recorded once more after the turn grammar changed, and the model did not make the error that time. The current recording therefore shows the block but not the correction.

## 2. Sections to complete in Phase 8

* Permission tiers and the hard-coded invariants.
* Write-path enforcement, including the one documented build-time exception for provisioning, risk R-01.
* The audit chain: tamper-evident with a published head hash, not tamper-proof, risk R-03.
* Prompt-injection containment and the adversarial fixtures.
* The model-swap demonstration that safety holds across models.
* The known-risk register.
