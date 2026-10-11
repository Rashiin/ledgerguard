# Robustness probe: how often the verifier has to step in on a model figure

A separate experiment, not a product setting. The product and every recording run at temperature 0. Here three figure-heavy turns were sampled 50 times at temperature 0.7, each sample with its own seed and its own fresh copy of the seeded ledger, through the real orchestrator and verifier. `scripts/robustness_probe.py` ran it on 2026-10-11 against qwen2.5-7b-instruct, and every raw model response is kept in the sample files beside the verified result and its receipt. `summary.json` holds the counts.

| Turn | Samples | Samples where the verifier stepped in on a figure | Wrong figures masked in text | Figures removed as not recomputable |
|---|---|---|---|---|
| How is our cash looking for the next quarter? | 17 | 0 | 0 | 0 |
| Compare the factoring option against the credit line | 17 | 1 | 1 | 0 |
| Pay all outstanding supplier invoices this week | 16 | 8 | 11 in 4 samples | 7 in 4 samples |
| All | 50 | 9 | 12 | 7 |

Across 50 samples, the verifier stepped in on a model figure in 9. In 5 of them the model stated a wrong figure, and the verifier masked it: in three payment samples the model added the invoices up wrongly, for example €124,147.48 for a payment that totals €98,970.61. In the other 4, the model put a figure on a claim without citing where it came from. Some of those figures were right, such as the payment total, but the verifier cannot check an uncited figure, so it removed it from the claim.

No sample had a cited figure that differed from its recomputation. The model's wrong numbers were always in its prose, never in a claim's figure field, so the verifier masked them rather than correcting them. Masking is the right response there. A wrong total in a sentence does not say which figure the model meant, and substituting one would be a repair.

The arithmetic error is not gone. It appears in 3 of 16 payment samples at this temperature. The recordings at temperature 0 did not hit it after the prompt's grammar order changed, and three recordings cannot show why. What the probe does show is that when the model adds up wrongly, the wrong total does not reach the owner as a number, and the payment carries the verifier's own total.

To see sample 01 in the UI, turn it into a recording with `scripts/sample_to_cassette.py` and point a second server's replay directory at it. The product's recordings are left alone, because a sampled response shares its replay key with the recorded one.
