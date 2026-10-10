# Fixtures

Everything in this directory is synthetic. No row describes a real business,
person, customer, supplier, lender or offer.

| File | What it is |
|---|---|
| `financing_products.json` | 12 invented financing products across 5 kinds. Providers are named "Synthetic Bank A" etc. Rates and fees are invented and are **not offers**. |
| `manifest.json` | Written by `scripts/seed.py`: the ids of the planted demo hooks below, the calibrated balances and the 13-week closing balances. |
| `llm_cassettes/` | Recorded LLM responses for `ReplayClient` (from Phase 4). |

## The sandbox business

**Nordvik Supply Co. Ltd** is an invented Irish B2B kitchen-equipment distributor:
about €4M net revenue a year, 18 employees, seasonal demand (spring and autumn
refit peaks), several large customers that pay late.

`uv run python scripts/seed.py --profile nordvik --seed 42` generates:

* 24 months of history (2024-10-17 to 2026-10-16), with sales invoices,
  supplier invoices, settlements, payroll, VAT returns, rent, loan repayments,
  utilities, fuel, insurance, bank fees, two dividends, a delivery van and a
  warehouse racking purchase paid on the as-of date;
* forward rows: scheduled payroll runs and obligations (rent, loan, software,
  VAT, corporation tax) up to the end of projection week 13 (2027-01-17), and
  the confirmed order book as `draft` invoices up to 2026-11-20;
* the operating account, an undrawn €100,000 revolving credit facility and petty cash.

The ledger's "today" is **Friday 2026-10-16** (`business.data_as_of`). Every
projection uses that date, never the wall clock, so the demo gives the same
answer whenever it is run. Projection week 1 starts Monday 2026-10-19.

The same seed gives a byte-identical SQLite file. All amounts are generated as
integer cents; no floating-point arithmetic is involved.

## Planted demo hooks

These are built deliberately, not left to chance. Nothing else is hidden in the
data. Ids and amounts below are for seed 42; `manifest.json` has them for the
last seed run.

| Hook | Rows (seed 42) | Detail |
|---|---|---|
| Projected shortfall in weeks 6-8 | weeks 6, 7, 8 | All-classes closing balances: −€20,086.18, −€219,676.22 (worst), −€205,605.30. Every other week stays at least €18,000 above zero. |
| Cause: refit stock paid before the customer pays | `inv_0665` → `inv_0678` | Nordvik pays Halvorsen Steelworks €142,500.00 for refit stock on 2026-12-01 (week 7). Brennan's refit milestone `inv_0678` (€227,550.00, issued 2026-10-16, due 2026-11-20) is expected 26 days late, on 2026-12-16 (week 9). |
| Also in the window | `obl_0004`, `pay_0026` | VAT return €155,391.41 due 2026-11-23 and payroll €73,576.18 on 2026-11-27, both in week 6. |
| Payroll run inside the shortfall window | `pay_0026` | November payroll, pay date 2026-11-27. |
| Customer above 30% revenue concentration | `cp_brennan` | Brennan Hospitality Group, about 37% of trailing-12-month net revenue. No other customer is above 30%. |
| Three overdue receivables | `inv_0412`, `inv_0625`, `inv_0654` | `inv_0412` is Brennan's €46,854.00 invoice due 2026-10-02 (the "invoice 0412" of scenario 7). |
| One disputed receivable | `inv_0620` | Harbourview Hotels, €12,400.00, damaged door seal. Excluded from expected receipts. |
| Duplicate-looking supplier invoice pair | `inv_0641` (legitimate, paid 2026-10-08) and `inv_0377` (true duplicate, unpaid) | Delmar Smallwares, both €4,312.50, issued 3 days apart, same vendor reference DSW-26-0918. Paying `inv_0377` (scenario 8) falls within 14 days of the payment of `inv_0641`. |
| One anomalous transaction | `txn_0869` | Fuel-card charge of €1,121.64 on 2026-09-22, about 8 robust standard deviations (median/MAD) above the weekly fuel settlements. It is the only transaction above 5. |

## Confidence classes in the projection

Every projected line is tagged with one class. Classes are reported separately
and never merged into one number.

| Class | What it is | Source rows |
|---|---|---|
| `CONTRACTED` | Amount and counterparty are fixed | Issued invoices (`sent`, `part_paid`, `overdue`), scheduled payroll runs, obligations |
| `EXPECTED` | Confirmed order book, not yet invoiced | `draft` invoices, issued up to 2026-11-20 |
| `SPECULATIVE` | Modelled only | Seasonal fill: after the order book ends, each counterparty is assumed to repeat what it invoiced in the same week last year. Each line cites the paid invoice it repeats. |

Closing balances for seed 42, all in €k:

| Week | 1 | 2 | 3 | 4 | 5 | 6 | 7 | 8 | 9 | 10 | 11 | 12 | 13 |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| Contracted only | 182 | 145 | 134 | 107 | 159 | −47 | −239 | −256 | −13 | −13 | −101 | −102 | −102 |
| + Expected | 182 | 145 | 106 | 79 | 180 | −20 | −220 | −206 | 88 | 138 | 10 | 9 | 70 |
| + Speculative (full model) | 182 | 145 | 106 | 79 | 180 | −20 | −220 | −206 | 88 | 138 | 18 | 75 | 97 |

On contracted lines alone, cash goes negative in week 6 and does not recover
inside the 13 weeks. The recovery from week 9 depends on the order book and,
from week 11, on the seasonal fill.

## How the shortfall is placed

The seeder runs the same 13-week projection the application uses
(`ledgerguard.analytics.cashflow`). It then solves for a few amounts so that
weeks 6-8 close at least €20,000 below zero and every other week closes at
least €18,000 above it:

1. **Cash on hand at the as-of date.**
2. **Brennan's refit milestone** (`inv_0678`, €185,000.00 net for seed 42).
3. **A first instalment on the Halvorsen refit stock, due in week 6.** It is
   only created when the noise in a seed's early weeks needs it. Seed 42 does
   not.

The opening balance 24 months earlier is then set so the transaction history
arrives at the solved cash. If that history would ever drop below €15,000, the
warehouse racking purchase on the as-of date (`txn_0914`, €127,400.00 for
seed 42) is enlarged by the shortfall. That raises every earlier balance
without changing the projection.

The derivation is in the `solve_calibration` docstring. A test checks that a
range of other seeds also calibrate.

## Modelling notes a judge may notice

* **Invoice numbers are not strictly chronological.** The demo script refers to
  "invoice 0412" and "supplier invoice 0377". Those two numbers are swapped
  onto the planted rows after sequential numbering.
* **`draft` invoices are the confirmed order book.** A `draft` receivable is a
  confirmed sales order scheduled for billing. A `draft` payable is a confirmed
  purchase order not yet invoiced by the supplier. Both are projected as
  `EXPECTED`, never as `CONTRACTED`.
* **Late payments never cross the as-of date unless planted.** Only the four
  planted receivables are past due at 2026-10-16. A historical late payment
  that would have crossed that date is settled at a random business day before
  it instead.
* **Halvorsen Steelworks AS is outside the EU.** Its invoices carry no VAT
  (reverse charge).
* **The first VAT return in the history** (Sep-Oct 2024, paid 2024-11-22) is
  estimated from the following full period, because its early sales predate
  the ledger.
* **Not in the projection:** fuel, utilities, insurance and bank fees are
  variable spend with no invoice or obligation row. They total about €4,000 a
  month and are left out of the 13-week projection.
