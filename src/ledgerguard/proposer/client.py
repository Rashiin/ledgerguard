"""Model clients. The verifier never sees which one produced an output.

``LocalClient`` talks to Ollama on this machine and passes the turn's JSON
schema as ``format``, so the runtime constrains generation to that schema.
``ReplayClient`` answers from recorded cassettes with no model server and no
network. ``RecordingClient`` writes those cassettes from live runs.
``OpenAICompatibleClient`` is optional and nothing requires it. Retries live in
the orchestrator, so every client gets them.
"""

from __future__ import annotations

import hashlib
import json
import os
import time
import urllib.request
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ledgerguard.config import LLMClientKind, LocalModelConfig, ModelConfig


@dataclass(frozen=True)
class LLMResponse:
    content: str  # exactly what the model produced, never edited
    model: str
    elapsed_ms: int
    meta: dict[str, Any] = field(default_factory=dict)


class LLMClient(ABC):
    @abstractmethod
    def complete(self, messages: list[dict[str, str]], schema: dict[str, Any]) -> LLMResponse:
        """Return one completion constrained to ``schema`` where the runtime supports it."""


class LocalClient(LLMClient):
    def __init__(self, config: LocalModelConfig, seed: int | None = None) -> None:
        self._config = config
        self._seed = seed

    def complete(self, messages: list[dict[str, str]], schema: dict[str, Any]) -> LLMResponse:
        options: dict[str, Any] = {
            "temperature": self._config.temperature,
            "num_ctx": self._config.num_ctx,
        }
        if self._seed is not None:
            options["seed"] = self._seed
        body = {
            "model": self._config.model,
            "stream": False,
            "format": schema,
            "options": options,
            "messages": messages,
        }
        request = urllib.request.Request(
            f"{self._config.base_url}/api/chat",
            json.dumps(body).encode("utf-8"),
            {"Content-Type": "application/json"},
        )
        started = time.monotonic_ns()
        with urllib.request.urlopen(request, timeout=self._config.request_timeout_seconds) as r:
            reply = json.load(r)
        return LLMResponse(
            content=reply["message"]["content"],
            model=self._config.model,
            elapsed_ms=(time.monotonic_ns() - started) // 1_000_000,
            meta={k: reply.get(k) for k in ("prompt_eval_count", "eval_count", "done_reason")},
        )


# --------------------------------------------------------------------------- replay


def request_key(model: str, messages: list[dict[str, str]], schema: dict[str, Any]) -> str:
    """A stable fingerprint of one request. Any change to the prompt changes it."""
    canonical = json.dumps(
        {"model": model, "messages": messages, "schema": schema},
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


class ReplayMiss(Exception):
    """No recorded response matches this request."""


class ReplayClient(LLMClient):
    """Answers from recorded cassettes, with no model server and no network.

    A request is matched by its fingerprint. If the prompt, the tools' facts or the
    schema change, the fingerprint changes and the turn fails loudly instead of
    replaying an answer to a different question.
    """

    def __init__(self, cassette_dir: Path) -> None:
        self._responses: dict[str, dict[str, Any]] = {}
        for path in sorted(cassette_dir.glob("*.json")):
            cassette = json.loads(path.read_text(encoding="utf-8"))
            for item in cassette["interactions"]:
                self._responses[item["key"]] = {**item, "cassette": path.name}

    def complete(self, messages: list[dict[str, str]], schema: dict[str, Any]) -> LLMResponse:
        model = next(iter({r["model"] for r in self._responses.values()}), "replay")
        key = request_key(model, messages, schema)
        found = self._responses.get(key)
        if found is None:
            raise ReplayMiss(
                f"no recorded response for request {key[:12]}; record it against "
                "the local model first"
            )
        return LLMResponse(
            content=found["response"],
            model=found["model"],
            elapsed_ms=0,
            meta={"replayed_from": found["cassette"], "key": key},
        )


class RecordingClient(LLMClient):
    """Wraps a live client and writes every exchange to a cassette file."""

    def __init__(self, inner: LLMClient, path: Path, model: str) -> None:
        self._inner = inner
        self._path = path
        self._model = model
        self._interactions: list[dict[str, Any]] = []

    def complete(self, messages: list[dict[str, str]], schema: dict[str, Any]) -> LLMResponse:
        reply = self._inner.complete(messages, schema)
        self._interactions.append(
            {
                "key": request_key(self._model, messages, schema),
                "model": self._model,
                "response": reply.content,
                "meta": {**reply.meta, "elapsed_ms": reply.elapsed_ms},
            }
        )
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.write_text(
            json.dumps(
                {"cassette": self._path.stem, "interactions": self._interactions},
                indent=1,
                ensure_ascii=False,
            )
            + "\n",
            encoding="utf-8",
        )
        return reply


# --------------------------------------------------------------------------- hosted


class OpenAICompatibleClient(LLMClient):
    """Any OpenAI-style chat endpoint. Optional, and nothing depends on it.

    The schema is sent as a JSON-schema response format without strict mode.
    Strict mode requires every property to be required, and the turn schemas have
    optional fields. How far the endpoint enforces the schema is up to it, so the
    parser and the retries carry the guarantee, as they do for every client.
    """

    def __init__(self, base_url: str, model: str, api_key: str, timeout: int = 120) -> None:
        self._base_url = base_url.rstrip("/")
        self._model = model
        self._api_key = api_key
        self._timeout = timeout

    def complete(self, messages: list[dict[str, str]], schema: dict[str, Any]) -> LLMResponse:
        fmt: dict[str, Any] = {
            "type": "json_schema",
            "json_schema": {"name": "action_intent", "schema": schema, "strict": False},
        }
        body = {
            "model": self._model,
            "messages": messages,
            "response_format": fmt,
            "temperature": 0,
        }
        request = urllib.request.Request(
            f"{self._base_url}/chat/completions",
            json.dumps(body).encode("utf-8"),
            {"Content-Type": "application/json", "Authorization": f"Bearer {self._api_key}"},
        )
        started = time.monotonic_ns()
        with urllib.request.urlopen(request, timeout=self._timeout) as r:
            reply = json.load(r)
        return LLMResponse(
            content=reply["choices"][0]["message"]["content"],
            model=self._model,
            elapsed_ms=(time.monotonic_ns() - started) // 1_000_000,
            meta={"usage": reply.get("usage")},
        )


def make_client(config: ModelConfig, root: Path, seed: int | None = 42) -> LLMClient:
    """The client the configuration names. Replay needs nothing but the cassettes."""
    if config.client is LLMClientKind.REPLAY:
        return ReplayClient(root / config.replay.cassette_dir)
    if config.client is LLMClientKind.OPENAI_COMPATIBLE:
        oc = config.openai_compatible
        key = os.environ.get(oc.api_key_env, "")
        if not (oc.base_url and oc.model and key):
            raise ValueError("openai_compatible needs base_url, model and the API key variable")
        return OpenAICompatibleClient(oc.base_url, oc.model, key)
    return LocalClient(config.local, seed=seed)
