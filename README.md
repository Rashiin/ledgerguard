# LedgerGuard

LedgerGuard is a finance copilot for small and medium businesses in which the language model is never allowed to act directly. The model proposes a typed action. Deterministic code recomputes every figure from the ledger, applies business rules and assigns a permission tier. Only then does anything run or go to a person for approval.

It was built for FinTechathon 2026, International Track, Topic E.

> **Status: Phase 3 of 8.** The synthetic ledger, the 13-week cash projection, the audit log, the Executor and the full verifier are working. The language-model proposer, the three capabilities and the web UI come next. Lines marked *later phase* describe parts that are not built yet.

## Runs offline, on your own hardware

LedgerGuard runs on the business owner's machine against an open-weight model served locally by [Ollama](https://ollama.com). Invoices, bank transactions and payroll figures are never sent to a model provider. Every figure and every decision is checked by deterministic code, so a smaller local model can make the copilot less helpful but cannot let it do something unsafe. Pull the default model with:

```bash
ollama pull qwen2.5:7b-instruct
```

The model is needed only for live local runs, from Phase 4. The tests, the demo and the Docker image replay recorded model responses with `LLM_CLIENT=replay`. They need no model server, no API key and no network.

## Quickstart on your machine

You need [uv](https://docs.astral.sh/uv/). It installs and pins Python 3.11 by itself.

```bash
uv sync
uv run python scripts/seed.py --profile nordvik --seed 42
uv run ledgerguard-api
```

Open http://127.0.0.1:8000/api/health to check it is running. `LEDGERGUARD_PORT` changes the port.

| Endpoint | Returns |
|---|---|
| `GET /api/ledger/summary` | Cash, accounts, open invoices by status and the next payroll run |
| `GET /api/ledger/rows/{table}/{id}` | One source row, so any figure can be traced to what it came from |
| `GET /api/audit?from_seq=&limit=` | Audit events |
| `GET /api/audit/verify` | Whether the audit chain is intact, its length and its head hash |
| `GET /api/receipts/{id}` | One Trust Receipt |
| `GET /api/approvals` | Actions waiting for approval, with their rules, flags and figures |
| `POST /api/approvals/{id}/approve` | Body `{"approver": "operator", "confirmation": "CONFIRM int_…"}`. The confirmation is required for T3. |
| `POST /api/approvals/{id}/decline` | Body `{"by": "operator", "reason": "…"}` |

On first start the app creates `data/verifier.key`. That key seals verified actions. Only the owner can read it and it is never committed.

Run the checks with:

```bash
uv run pytest
uv run ruff check src tests scripts
uv run mypy
```

## Quickstart with Docker

```bash
docker compose up
```

Open http://localhost:8080/api/health. `LEDGERGUARD_HOST_PORT` changes the host port. The image contains the seeded ledger and starts with `LLM_CLIENT=replay`, so it needs no model server and no network. The Docker path is verified in Phase 7.

## The sandbox ledger

All data is synthetic. Nordvik Supply Co. is an invented kitchen-equipment distributor with about €4M revenue, 18 employees, seasonal demand and customers who pay late. The same seed always produces a byte-identical database.

The situations the demo relies on are planted on purpose and listed in [fixtures/README.md](fixtures/README.md):

* a cash shortfall in weeks 6 to 8;
* one customer above 30% of revenue;
* three overdue invoices and one disputed;
* a duplicate supplier invoice;
* one anomalous transaction;
* a payroll run inside the shortfall.

## Configuration

`config/policy.yaml` holds the outbound ceiling, the T3 cool-off, account floors and rule parameters. The app refuses to start if the file has an unknown key or a bad value. Some invariants live in code so that this file cannot weaken them. Payments and accepting a financing offer are always T3, and a block is never overridden.

`config/model.yaml` chooses the model client: `local`, `openai_compatible` or `replay`. The `LLM_CLIENT` environment variable overrides it.

## Project layout

```
src/ledgerguard/
  money.py              money as Decimal; floats are refused
  config.py             policy and model configuration, validated at startup
  domain/               closed vocabularies and the VAT rate
  db/                   models, column types, the read-only engine
  migrations/           Alembic migrations
  provisioning.py       offline migrate and seed, the one writer besides the Executor
  analytics/            the 13-week projection, as pure functions
  ledger/               read-only ledger queries
  audit/                the hash chain
  verifier/             the six verification stages, the Trust Receipt and the seal
  rules/                the eight business rules
  service.py            turns and the approval queue
  executor/             the only runtime writer
  seed/nordvik.py       the synthetic ledger generator
  api/app.py            the HTTP API
scripts/seed.py         builds data/ledger.sqlite and fixtures/manifest.json
docs/ARCHITECTURE.md    how the system is designed
docs/DECISIONS.md       the judgement calls behind it
docs/RISK_REGISTER.md   known residual risks
THIRD_PARTY.md          every dependency and its licence
```

## Licences

Every dependency and its licence is listed in [THIRD_PARTY.md](THIRD_PARTY.md). All code in this repository was written for this competition.
