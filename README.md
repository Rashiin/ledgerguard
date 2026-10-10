# LedgerGuard

An AI finance copilot for small and medium businesses in which **the language
model is never allowed to act directly**. The model proposes a typed action.
A deterministic verifier recomputes every figure from source rows, enforces
business rules and assigns a permission tier. Only then is anything executed,
or routed to a human for approval.

FinTechathon 2026 · International Track · Topic E.

> **Status: Phase 1 (foundation).** Data model, migrations, the synthetic
> ledger and configuration are in place. The verifier, proposer, capabilities
> and UI arrive in later phases. Sections marked *(later phase)* describe what
> is coming.

## Runs offline, on your own hardware

LedgerGuard is built to run on the business owner's own machine, against a
local open-weight model served by [Ollama](https://ollama.com). No invoices,
bank transactions or payroll figures are sent to a third-party model
provider. Correctness lives in the deterministic verifier, not in the model,
so a smaller local model affects how helpful the copilot is, never what it is
allowed to do. The default model is pulled with:

```bash
ollama pull qwen2.5:7b-instruct
```

The model is only needed for live local runs *(from Phase 4)*. The demo, the
tests and the Docker image use recorded responses (`LLM_CLIENT=replay`) and
need no model server, no API key and no network.

## Quickstart (local)

Requires [uv](https://docs.astral.sh/uv/). uv installs and pins Python 3.11
itself.

```bash
uv sync
uv run python scripts/seed.py --profile nordvik --seed 42
uv run ledgerguard-api
```

Then open http://127.0.0.1:8000/api/health. Set `LEDGERGUARD_PORT` to use a
different port.

Run the checks:

```bash
uv run pytest
uv run ruff check src tests scripts
uv run mypy
```

## Quickstart (Docker, for judges)

```bash
docker compose up
```

The image builds the seeded ledger in and starts with `LLM_CLIENT=replay`. No
model server is started and no network access is needed. *(Verified in Phase 7.)*

## The sandbox ledger

All data is synthetic. **Nordvik Supply Co.** is an invented B2B
kitchen-equipment distributor: about €4M revenue, 18 employees, and seasonal,
late-paying customers. The seeder is deterministic: the same seed produces a
byte-identical database. The planted demo situations are listed openly in
[fixtures/README.md](fixtures/README.md):

* a cash shortfall in weeks 6-8;
* a customer above 30% revenue concentration;
* overdue and disputed invoices;
* a duplicate supplier invoice;
* an anomalous transaction;
* a payroll run inside the shortfall.

## Configuration

* `config/policy.yaml`: permission-tier ceilings, the T3 cool-off,
  account floors and rule parameters. It is validated strictly at boot.
  Invariants such as "accepting a financing offer is always T3" are in code
  and cannot be configured away.
* `config/model.yaml`: LLM client selection (`local`, `openai_compatible`,
  `replay`). The `LLM_CLIENT` environment variable overrides it.

## Project layout

```
src/ledgerguard/
  money.py          Decimal-only money; floats are refused
  config.py         strict policy and model configuration
  domain/enums.py   closed vocabularies (action kinds, tiers, statuses)
  db/               models, column types, read-only engine
  migrations/       Alembic migrations
  provisioning.py   offline: migrate + seed (the only writer besides the Executor)
  analytics/        13-week projection as pure functions
  seed/nordvik.py   synthetic ledger generator
  api/app.py        FastAPI app (read-only)
scripts/seed.py     builds data/ledger.sqlite and fixtures/manifest.json
docs/DECISIONS.md   architecture decisions and their reasons
THIRD_PARTY.md      every dependency and its licence
```

## Licences

See [THIRD_PARTY.md](THIRD_PARTY.md). All of the code in this repository was
written for this competition.
