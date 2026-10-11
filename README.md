# LedgerGuard

LedgerGuard is a finance copilot for small and medium businesses in which the language model is never allowed to act directly. The model proposes a typed action. Deterministic code recomputes every figure from the ledger, applies business rules and assigns a permission tier. Only then does anything run or go to a person for approval.

It was built for FinTechathon 2026, International Track, Topic E.

> **Status: Phase 4 of 8, in progress.** The synthetic ledger, the 13-week cash projection, the audit log, the Executor and the full verifier are working, including the check that an answer does not leave out what mattered. The local model now answers through a schema it cannot break and cites facts that code computed. The turn loop, the three capabilities and the web UI come next. Lines marked *later phase* describe parts that are not built yet.

## Runs offline, on your own hardware

LedgerGuard runs on the business owner's machine against an open-weight model served locally by [Ollama](https://ollama.com). Invoices, bank transactions and payroll figures are never sent to a model provider. Every figure and every decision is checked by deterministic code, so a smaller local model can make the copilot less helpful but cannot let it do something unsafe.

The default model is Qwen2.5-7B-Instruct at q4_k_m, about 4.7 GB, and LedgerGuard expects it under the name `qwen2.5-7b-instruct`. Where the Ollama registry is reachable, pull it and give it that name:

```bash
ollama pull qwen2.5:7b-instruct
ollama cp qwen2.5:7b-instruct qwen2.5-7b-instruct
```

The Ollama registry is not reachable from every region or network. The fallback is to build the model from the official GGUF files on Hugging Face, which is how it was installed for development. Download both shards of the q4_k_m quant from [Qwen/Qwen2.5-7B-Instruct-GGUF](https://huggingface.co/Qwen/Qwen2.5-7B-Instruct-GGUF) into one directory, copy `models/Modelfile.qwen2.5-7b-instruct` from this repository next to them, and build:

```bash
ollama create qwen2.5-7b-instruct -f Modelfile.qwen2.5-7b-instruct
```

The Modelfile names both shards. Naming only the first fails with "has 1 shards, expected 2". The shards used in development have these SHA-256 digests:

| File | Bytes | SHA-256 |
|---|---|---|
| `qwen2.5-7b-instruct-q4_k_m-00001-of-00002.gguf` | 3,993,201,344 | `dfce12e3862a5283ccfb88221b48480e58745165de856439950d0f22590580db` |
| `qwen2.5-7b-instruct-q4_k_m-00002-of-00002.gguf` | 689,872,288 | `539cf93f78e887edea1c04e2d7d8cdaca9d01dae9c9025bcb8accbe29df3d72a` |

The model is needed only for live local runs, from Phase 4. The tests, the demo and the Docker image replay recorded model responses with `LLM_CLIENT=replay`. They need no model server, no API key and no network.

## Quickstart on your machine

You need [uv](https://docs.astral.sh/uv/). It installs and pins Python 3.11 by itself.

You also need Node.js 22 or later to build the web UI.

```bash
uv sync
uv run python scripts/seed.py --profile nordvik --seed 42
(cd web && npm ci && npm run build)
LLM_CLIENT=replay uv run ledgerguard-api
```

Open http://127.0.0.1:8000 for the UI. `LEDGERGUARD_PORT` changes the port. With `LLM_CLIENT=replay` the recorded scenarios run with no model server. Leave it out to use the local model that `config/model.yaml` names.

Recordings match the exact request, including the facts drawn from the ledger. Approving an action changes the ledger, so requests recorded on the fresh ledger stop replaying after it. Run the read-only scenarios and the adversarial fixtures first, then make the approval requests, and only then work through the Approval Inbox. `scripts/export_evidence.py` runs the whole demo in that order and writes `evidence/`.

To run the approval scenarios a second time, seed the ledger again first with `uv run python scripts/seed.py --profile nordvik --seed 42`, or recreate the Docker container with `docker compose down` and `docker compose up`. Without that, those requests answer "no recording exists". That is risk R-22 working as designed, not a fault.

The UI has four screens. The Console streams each turn's steps, marked by whether code, the model or the verifier took them, and shows the verified answer with a Trust Receipt beside it. The Cash-Flow Radar shows the 13-week projection by confidence class, the alerts, the scenario simulator and the financing matcher. The Approval Inbox shows each pending action with the ledger changes approving it would make. The Audit screen walks the hash chain and lists every event.

For frontend work, run the API on port 8610 and `npm run dev` in `web/`. Vite serves on port 5610 and proxies `/api` to the API.

| Endpoint | Returns |
|---|---|
| `GET /api/ledger/summary` | Cash, accounts, open invoices by status and the next payroll run |
| `GET /api/ledger/rows/{table}/{id}` | One source row, so any figure can be traced to what it came from |
| `GET /api/audit?from_seq=&limit=` | Audit events |
| `GET /api/audit/verify` | Whether the audit chain is intact, its length and its head hash |
| `GET /api/receipts/{id}` | One Trust Receipt |
| `POST /api/chat` | Body `{"message": "…"}`. A server-sent event stream of the turn's steps, then the verified turn |
| `GET /api/cashflow/projection?weeks=13` | The weekly projection with each confidence class kept separate |
| `POST /api/cashflow/scenario` | Body `{"scenario": {"type": "customer_pays_late", "counterparty_id": "cp_brennan", "days": 30}}`. Before and after, and the change in alerts |
| `GET /api/alerts` | Radar alerts with severity, lead time and the rows behind them |
| `POST /api/financing/match` | Body `{"weights": {"cost": "40", "coverage": "20"}}`. Options ranked by total cost and fit, with a do-nothing baseline |
| `GET /api/analysis` | Margins, burn, runway, DSO, DPO, cash conversion cycle and anomalies, each with its method |
| `GET /api/approvals` | Actions waiting for approval, with their rules, flags and figures |
| `GET /api/approvals/{id}/diff` | The ledger changes approving the action would make, computed by the Executor's own handler and rolled back |
| `POST /api/approvals/{id}/approve` | Body `{"approver": "operator", "confirmation": "CONFIRM int_…"}`. The confirmation is required for T3. |
| `POST /api/approvals/{id}/decline` | Body `{"by": "operator", "reason": "…"}` |

On first start the app creates `data/verifier.key`. That key seals verified actions. Only the owner can read it and it is never committed.

Run the checks with:

```bash
uv run pytest
uv run ruff check src tests scripts
uv run mypy
(cd web && npm run lint && npm run typecheck)
```

`scripts/probe_grammar.py OUT_DIR` measures how the local runtime's schema grammar orders properties and whether it biases the model's choices. It needs Ollama with the default model and saves every raw request and response.

## Quickstart with Docker

```bash
docker compose up
```

Open http://localhost:8080 for the UI. `LEDGERGUARD_HOST_PORT` changes the host port. The image contains the seeded ledger and starts with `LLM_CLIENT=replay`, so it needs no model server and no network. The Docker path is verified in Phase 7.

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
  api/app.py            the HTTP API, which also serves the built UI
  proposer/             allowlist, prompts, the turn grammar, model clients, the turn loop
web/                    the four-screen UI: React, Vite, Tailwind, Recharts
scripts/seed.py         builds data/ledger.sqlite and fixtures/manifest.json
scripts/record_turn.py  records one turn against the local model for replay
scripts/probe_grammar.py  measures the runtime grammar's behaviour
docs/ARCHITECTURE.md    how the system is designed
docs/DECISIONS.md       the judgement calls behind it
docs/RISK_REGISTER.md   known residual risks
THIRD_PARTY.md          every dependency and its licence
```

## Licences

Every dependency and its licence is listed in [THIRD_PARTY.md](THIRD_PARTY.md). All code in this repository was written for this competition.
