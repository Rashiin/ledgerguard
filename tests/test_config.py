from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

from ledgerguard.config import LLMClientKind, load_model_config, load_policy
from ledgerguard.domain.enums import Tier

CONFIG = Path(__file__).resolve().parent.parent / "config"


def _policy_with(tmp_path: Path, **changes: object) -> Path:
    data = yaml.safe_load((CONFIG / "policy.yaml").read_text())
    for dotted, value in changes.items():
        node = data
        *parents, leaf = dotted.split("__")
        for p in parents:
            node = node[p]
        node[leaf] = value
    path = tmp_path / "policy.yaml"
    path.write_text(yaml.safe_dump(data))
    return path


def test_shipped_policy_loads() -> None:
    policy = load_policy(CONFIG / "policy.yaml")
    assert policy.amount_ceilings[Tier.T3] > policy.amount_ceilings[Tier.T1]
    assert policy.t3.cool_off_seconds >= 60


def test_float_money_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(ValidationError):
        load_policy(_policy_with(tmp_path, amount_ceilings__T1=50000.5))


def test_unknown_key_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(ValidationError):
        load_policy(_policy_with(tmp_path, rules__llm_may_override=True))


def test_cool_off_cannot_drop_below_sixty_seconds(tmp_path: Path) -> None:
    with pytest.raises(ValidationError):
        load_policy(_policy_with(tmp_path, t3__cool_off_seconds=5))


def test_t3_confirmation_cannot_be_disabled(tmp_path: Path) -> None:
    with pytest.raises(ValidationError):
        load_policy(_policy_with(tmp_path, t3__require_typed_confirmation=False))


def test_ceilings_must_increase(tmp_path: Path) -> None:
    with pytest.raises(ValidationError):
        load_policy(_policy_with(tmp_path, amount_ceilings__T1="900000.00"))


def test_model_config_defaults_to_local_and_env_overrides(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("LLM_CLIENT", raising=False)
    assert load_model_config(CONFIG / "model.yaml").client is LLMClientKind.LOCAL
    monkeypatch.setenv("LLM_CLIENT", "replay")
    assert load_model_config(CONFIG / "model.yaml").client is LLMClientKind.REPLAY


def test_retries_are_capped_at_two(tmp_path: Path) -> None:
    data = yaml.safe_load((CONFIG / "model.yaml").read_text())
    data["max_intent_retries"] = 3
    path = tmp_path / "model.yaml"
    path.write_text(yaml.safe_dump(data))
    with pytest.raises(ValidationError):
        load_model_config(path)
