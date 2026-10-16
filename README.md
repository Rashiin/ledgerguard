# LedgerGuard

LedgerGuard is a finance copilot for small and medium businesses in which the language model is never allowed to act directly. The model proposes a typed action. Deterministic code recomputes every figure from the ledger, applies business rules and assigns a permission tier. Only then does anything run or go to a person for approval.

It was built for FinTechathon 2026, International Track, Topic E. Repository: [github.com/Rashiin/ledgerguard](https://github.com/Rashiin/ledgerguard).

## For judges

| Deliverable | Where |
|---|---|
| Technical documentation | [docs/technical_documentation.pdf](docs/technical_documentation.pdf), built from [the Markdown source](docs/technical_documentation.md) |
| Security self-assessment and known-risk list | [docs/security_self_assessment.pdf](docs/security_self_assessment.pdf), built from [SECURITY_SELF_ASSESSMENT.md](docs/SECURITY_SELF_ASSESSMENT.md) and [RISK_REGISTER.md](docs/RISK_REGISTER.md) |
| Presentation deck | [docs/deck.pdf](docs/deck.pdf) |
| Demo video, 4 min | [docs/demo.mp4](docs/demo.mp4) |
| Execution evidence | [evidence/](evidence/README.md): every scenario turn, every Trust Receipt, the operator's decisions and the audit chain with its head hash |
| Measurements | [evidence/robustness](evidence/robustness/README.md), [evidence/heldout](evidence/heldout/README.md) and [evidence/model_swap](evidence/model_swap) |
| Design decisions | [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) and [docs/DECISIONS.md](docs/DECISIONS.md) |

The fastest way to see it working is `docker compose up` and http://localhost:8080.

**What is recorded, and what runs live.** Only the model's responses are recorded. The verifier, the rules, the ledger and the audit chain all run live on every replay. That is why a judge can reproduce every measured result offline, with no API key, no model server and no network. The recorded part is the only part we could not make deterministic.

**What to type.** The Console opens with every recorded request listed. Choose one from the list or the "Recorded requests" menu. A question typed in your own words has no recorded model response, so the Console answers "no recorded model response" and nothing runs. That is expected in the offline demo. To ask anything you like, run with a local model as described below.

**The order to follow.** Run the read-only scenarios and the injection fixtures first, then make the requests that need approval, and only then work through the Approval Inbox. Approving changes the ledger, and a request recorded against the fresh ledger no longer matches once it has changed, so it would also answer "no recorded model response". To start again, recreate the container with `docker compose down` and `docker compose up`.

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

The model is needed only for live local runs. The tests, the demo and the Docker image replay recorded model responses with `LLM_CLIENT=replay`. They need no model server, no API key and no network.

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

To run the approval scenarios a second time, seed the ledger again first with `uv run python scripts/seed.py --profile nordvik --seed 42`, or recreate the Docker container with `docker compose down` and `docker compose up`. Without that, those requests answer "no recorded model response". That is risk R-22 working as designed, not a fault.

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

Open http://localhost:8080 for the UI. `LEDGERGUARD_HOST_PORT` changes the host port. The image contains the seeded ledger and starts with `LLM_CLIENT=replay`, so it needs no model server and no network. The image has been run with networking switched off, and it replays every scenario and fixture.

## Reproducing the results

Every number in the documents comes from a script. The first two need nothing but the repository. The rest need Ollama with the model they name.

| Script | What it does |
|---|---|
| `uv run python scripts/export_evidence.py` | Runs all twelve scenarios and the fixtures offline in demo order, works the Approval Inbox, and writes `evidence/` |
| `uv run python scripts/build_docs.py` | Builds the documentation, the self-assessment and the deck to PDF, with headless Chrome |
| `uv run python scripts/record_turn.py --scenario ID` | Records one catalogued scenario against the local model |
| `uv run python scripts/probe_grammar.py OUT_DIR` | Measures how Ollama's grammar orders properties and whether it biases the model's choices |
| `uv run python scripts/robustness_probe.py OUT_DIR` | Samples three figure-heavy turns 50 times at temperature 0.7 and counts the verifier's corrections |
| `uv run python scripts/heldout_run.py` | Runs the four held-out injection fixtures once, and refuses if the neutraliser has changed |
| `uv run python scripts/model_swap_demo.py --replay` | Replays scenario 9 as answered by each local model, offline |
| `node scripts/capture_screens.mjs …` and `uv run python scripts/build_video.py` | Capture the screens from the running app and build the video |

## The sandbox ledger

All data is synthetic. Nordvik Supply Co. is an invented kitchen-equipment distributor with about €4M revenue, 18 employees, seasonal demand and customers who pay late. The same seed always produces identical data, and a byte-identical database file on any one platform.

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
