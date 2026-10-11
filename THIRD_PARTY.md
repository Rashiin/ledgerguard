# Third-party components

This file lists every component LedgerGuard uses and its licence. Versions are the ones pinned in `uv.lock`. Licences were read from each installed package's metadata.

## Shipped in the Docker image

| Package | Version | Licence | Pulled in by |
|---|---|---|---|
| fastapi | 0.143.0 | MIT | direct |
| uvicorn | 0.54.0 | BSD-3-Clause | direct |
| pydantic | 2.14.0 | MIT | direct |
| sqlalchemy | 2.1.4 | MIT | direct |
| alembic | 1.20.0 | MIT | direct |
| pyyaml | 6.0.3 | MIT | direct |
| starlette | 1.7.0 | BSD-3-Clause | fastapi |
| pydantic-core | 2.50.0 | MIT | pydantic |
| annotated-types | 0.8.0 | MIT | pydantic |
| typing-inspection | 0.4.4 | MIT | pydantic, fastapi |
| annotated-doc | 0.0.5 | MIT | fastapi |
| opentelemetry-api | 1.45.1 | Apache-2.0 | fastapi |
| anyio | 4.15.1 | MIT | starlette |
| idna | 3.20 | BSD-3-Clause | anyio |
| mako | 1.4.3 | MIT | alembic |
| markupsafe | 3.0.4 | BSD-3-Clause | mako |
| click | 8.5.0 | BSD-3-Clause | uvicorn |
| h11 | 0.16.0 | MIT | uvicorn |
| typing-extensions | 4.16.0 | **PSF-2.0** | pydantic, sqlalchemy, starlette |

`typing-extensions` is the only shipped package outside MIT, Apache and BSD. PSF-2.0 is a permissive, OSI-approved licence, and pydantic and SQLAlchemy both require the package.

## Shipped in the web bundle

The web UI in `web/` is built into static files that the API serves. These packages end up in that bundle. Versions are the ones pinned in `web/package-lock.json`, and licences were read with `license-checker-rseidelsohn` from the installed packages. ISC and BSD-3-Clause are permissive, OSI-approved licences.

| Package | Version | Licence |
|---|---|---|
| @babel/runtime | 7.29.10 | MIT |
| @types/d3-array | 3.2.2 | MIT |
| @types/d3-color | 3.1.3 | MIT |
| @types/d3-ease | 3.0.2 | MIT |
| @types/d3-interpolate | 3.0.4 | MIT |
| @types/d3-path | 3.1.1 | MIT |
| @types/d3-scale | 4.0.9 | MIT |
| @types/d3-shape | 3.2.0 | MIT |
| @types/d3-time | 3.0.4 | MIT |
| @types/d3-timer | 3.0.2 | MIT |
| clsx | 2.1.1 | MIT |
| csstype | 3.2.3 | MIT |
| d3-array | 3.2.4 | ISC |
| d3-color | 3.1.0 | ISC |
| d3-ease | 3.0.1 | BSD-3-Clause |
| d3-format | 3.1.2 | ISC |
| d3-interpolate | 3.0.1 | ISC |
| d3-path | 3.1.0 | ISC |
| d3-scale | 4.0.2 | ISC |
| d3-shape | 3.2.0 | ISC |
| d3-time-format | 4.1.0 | ISC |
| d3-time | 3.1.0 | ISC |
| d3-timer | 3.0.1 | ISC |
| decimal.js-light | 2.5.1 | MIT |
| dom-helpers | 5.2.1 | MIT |
| eventemitter3 | 4.0.7 | MIT |
| fast-equals | 5.4.4 | MIT |
| internmap | 2.0.3 | ISC |
| js-tokens | 4.0.0 | MIT |
| lodash | 4.18.1 | MIT |
| loose-envify | 1.4.0 | MIT |
| object-assign | 4.1.1 | MIT |
| prop-types | 15.8.1 | MIT |
| react-dom | 18.3.1 | MIT |
| react-is | 16.13.1 | MIT |
| react-is | 18.3.1 | MIT |
| react-smooth | 4.0.4 | MIT |
| react-transition-group | 4.4.5 | BSD-3-Clause |
| react | 18.3.1 | MIT |
| recharts-scale | 0.4.5 | MIT |
| recharts | 2.13.3 | MIT |
| scheduler | 0.23.2 | MIT |
| tiny-invariant | 1.3.3 | MIT |
| victory-vendor | 36.9.2 | MIT AND ISC |

## Used in development only

| Package | Version | Licence |
|---|---|---|
| pytest | 9.1.1 | MIT |
| pytest-asyncio | 1.4.0 | Apache-2.0 |
| pytest-cov | 7.1.0 | MIT |
| coverage | 7.16.2 | Apache-2.0 |
| ruff | 0.17.0 | MIT |
| mypy | 2.4.0 | MIT |
| httpx | 0.28.1 | BSD-3-Clause |
| httpcore | 1.0.9 | BSD-3-Clause |
| types-pyyaml | 6.0.12.20260906 | Apache-2.0 |
| pluggy, iniconfig, mypy-extensions, librt, ast-serialize | as pinned in uv.lock | MIT |
| pygments | 2.21.0 | BSD-2-Clause |
| packaging | 26.3 | Apache-2.0 OR BSD-2-Clause |
| certifi, through httpx | 2026.7.22 | **MPL-2.0** |
| pathspec, through mypy | 1.1.1 | **MPL-2.0** |
| vite | 5.4.11 | MIT |
| @vitejs/plugin-react | 4.3.4 | MIT |
| typescript | 5.6.3 | Apache-2.0 |
| tailwindcss | 3.4.15 | MIT |
| postcss | 8.4.49 | MIT |
| autoprefixer | 10.4.20 | MIT |
| eslint, @eslint/js | 9.15.0 | MIT |
| typescript-eslint | 8.15.0 | MIT |
| eslint-plugin-react-hooks | 5.0.0 | MIT |
| globals | 15.12.0 | MIT |
| @types/react, @types/react-dom | 18.3.12, 18.3.1 | MIT |

The frontend packages above build and check the web UI. Only their output, the static files, ships.

The two MPL-2.0 packages are used unmodified by the test and type-check tools. They are not distributed with the application.

## Toolchain

| Tool | Version | Licence |
|---|---|---|
| uv | 0.13.0 | MIT OR Apache-2.0 |
| CPython | 3.11.17 | PSF-2.0 |
| Node.js, used to build the web UI | 22 in the Docker build stage | MIT |

## Language models

Model weights are third-party components too. They are not in this repository or in the Docker image. The owner downloads them separately, and the README describes how.

| Model | Source and build | Licence | Role |
|---|---|---|---|
| Qwen2.5-7B-Instruct, q4_k_m | The two GGUF shards from [Qwen/Qwen2.5-7B-Instruct-GGUF](https://huggingface.co/Qwen/Qwen2.5-7B-Instruct-GGUF), built with Ollama 0.40.2 as `qwen2.5-7b-instruct` from `models/Modelfile.qwen2.5-7b-instruct`. The Ollama model id is `2cf59abf1e1b7009121b2aa7006d792d668c57ee476d6c9c9737685988f370fe`. The README lists the shard digests. | Apache-2.0 | default `LocalClient` model |
| Mistral-7B-Instruct v0.3 | Not installed yet. It is the second model for `scripts/model_swap_demo.py`. | Apache-2.0, to be confirmed against the files used | second model for the model-swap demonstration |

The Qwen2.5 licence was checked against the LICENSE file distributed with the 7B weights, which is the Apache License 2.0. Licences differ between sizes in the Qwen2.5 family. The 3B and 72B models use other terms, so the licence must be checked again if the model size changes.

## Data

All data is synthetic and generated by `scripts/seed.py`. No third-party dataset is used. The financing catalogue in `fixtures/financing_products.json` is invented and names no real provider or offer.
