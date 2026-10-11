"""Model clients. The verifier never sees which one produced an output.

``LocalClient`` talks to Ollama on this machine and passes the turn's JSON
schema as ``format``, so the runtime constrains generation to that schema.
Retries and the replay client are added with the turn loop.
"""

from __future__ import annotations

import json
import time
import urllib.request
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

from ledgerguard.config import LocalModelConfig


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
