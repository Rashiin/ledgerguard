"""LocalClient sends the turn's schema to Ollama and returns the reply untouched."""

from __future__ import annotations

import io
import json
from pathlib import Path
from typing import Any

import pytest

from ledgerguard.config import load_model_config
from ledgerguard.domain.enums import ActionKind
from ledgerguard.proposer import client as C
from ledgerguard.proposer.schema import intent_schema

from .conftest import ROOT


def test_request_carries_the_schema_and_reply_is_not_edited(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sent: dict[str, Any] = {}
    raw = '{"kind": "ANSWER_QUERY" , "weird spacing": true}'

    class Reply(io.BytesIO):
        def __enter__(self) -> Reply:
            return self

        def __exit__(self, *exc: object) -> None:
            return None

    def fake_urlopen(request: Any, timeout: int) -> Reply:
        sent["url"] = request.full_url
        sent["body"] = json.loads(request.data)
        sent["timeout"] = timeout
        return Reply(
            json.dumps(
                {
                    "message": {"content": raw},
                    "eval_count": 7,
                    "prompt_eval_count": 11,
                    "done_reason": "stop",
                }
            ).encode()
        )

    monkeypatch.setattr(C.urllib.request, "urlopen", fake_urlopen)
    config = load_model_config(Path(ROOT / "config" / "model.yaml")).local
    schema = intent_schema([ActionKind.ANSWER_QUERY])
    reply = C.LocalClient(config, seed=42).complete([{"role": "user", "content": "hi"}], schema)

    assert sent["url"] == "http://localhost:11434/api/chat"
    assert sent["body"]["format"] == schema
    assert sent["body"]["model"] == "qwen2.5-7b-instruct"
    assert sent["body"]["stream"] is False
    assert sent["body"]["options"]["seed"] == 42
    assert sent["timeout"] == config.request_timeout_seconds
    assert reply.content == raw
    assert reply.meta == {"prompt_eval_count": 11, "eval_count": 7, "done_reason": "stop"}
    assert isinstance(reply.elapsed_ms, int)
