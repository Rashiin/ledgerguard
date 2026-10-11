# The image judges run.
#
# It starts with LLM_CLIENT=replay, so the whole demo runs from recorded model
# responses with no model server, no API key and no network. Ollama is not in
# the image. Development against a local model happens on the host.
#
# The first stage builds the web UI into static files. Node is not in the final
# image.

FROM node:22.11.0-slim AS web
WORKDIR /web
COPY web/package.json web/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY web/ ./
RUN npm run build

FROM python:3.11-slim AS app

COPY --from=ghcr.io/astral-sh/uv:0.13.0 /uv /usr/local/bin/uv

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    PYTHONUNBUFFERED=1

WORKDIR /app

COPY pyproject.toml uv.lock .python-version ./
RUN uv sync --frozen --no-dev --no-install-project

COPY src ./src
COPY config ./config
COPY fixtures ./fixtures
COPY scripts ./scripts
COPY --from=web /web/dist ./web/dist
RUN uv sync --frozen --no-dev

# The ledger is deterministic, so it is built into the image.
RUN uv run --no-sync python scripts/seed.py --profile nordvik --seed 42

ENV LLM_CLIENT=replay \
    LEDGERGUARD_DB=/app/data/ledger.sqlite \
    LEDGERGUARD_CONFIG_DIR=/app/config \
    LEDGERGUARD_WEB=/app/web/dist \
    LEDGERGUARD_HOST=0.0.0.0 \
    LEDGERGUARD_PORT=8000

EXPOSE 8000
CMD ["uv", "run", "--no-sync", "ledgerguard-api"]
