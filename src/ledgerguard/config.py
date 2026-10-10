"""Configuration loading.

Two YAML files live in ``config/``: ``policy.yaml`` (permission tiers,
ceilings, rule parameters) and ``model.yaml`` (LLM client selection). Both
are parsed into strict Pydantic models at boot; an unknown key, a float where
money is expected, or an out-of-range value stops the application from
starting rather than being ignored.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum
from pathlib import Path
from typing import Annotated, Any

import yaml
from pydantic import BaseModel, BeforeValidator, ConfigDict, Field, model_validator

from ledgerguard.domain.enums import Tier
from ledgerguard.money import to_money

MoneyField = Annotated[Decimal, BeforeValidator(to_money)]


def _ratio(value: Any) -> Decimal:
    if not isinstance(value, str | Decimal):
        raise ValueError('ratios must be written as quoted strings, e.g. "0.30"')
    ratio = Decimal(value)
    if not Decimal(0) <= ratio <= Decimal(1):
        raise ValueError("ratio must be between 0 and 1")
    return ratio


RatioField = Annotated[Decimal, BeforeValidator(_ratio)]


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)


class T3Gate(Strict):
    cool_off_seconds: int = Field(ge=60)
    require_typed_confirmation: bool

    @model_validator(mode="after")
    def _confirmation_is_mandatory(self) -> T3Gate:
        if not self.require_typed_confirmation:
            raise ValueError("T3 typed confirmation cannot be disabled")
        return self


class RuleParameters(Strict):
    duplicate_payment_window_days: int = Field(ge=1, le=90)
    payroll_lead_days: int = Field(ge=0, le=30)
    tax_reserve_horizon_days: int = Field(ge=1, le=180)
    concentration_warning_share: RatioField


class Policy(Strict):
    version: int
    currency: str = Field(pattern=r"^[A-Z]{3}$")
    # An action whose amount exceeds its tier's ceiling escalates one tier;
    # above the T3 ceiling the AmountCeiling rule blocks it outright.
    amount_ceilings: dict[Tier, MoneyField]
    t3: T3Gate
    account_floors: dict[str, MoneyField]
    rules: RuleParameters

    @model_validator(mode="after")
    def _ceilings_are_ordered(self) -> Policy:
        required = {Tier.T1, Tier.T2, Tier.T3}
        missing = required - set(self.amount_ceilings)
        if missing:
            raise ValueError(f"amount_ceilings missing tiers: {sorted(missing)}")
        c = self.amount_ceilings
        if not c[Tier.T1] <= c[Tier.T2] <= c[Tier.T3]:
            raise ValueError("amount ceilings must be non-decreasing from T1 to T3")
        return self


class LLMClientKind(StrEnum):
    LOCAL = "local"
    OPENAI_COMPATIBLE = "openai_compatible"
    REPLAY = "replay"


class LocalModelConfig(Strict):
    base_url: str
    model: str
    request_timeout_seconds: int = Field(ge=1)
    temperature: Annotated[float, Field(ge=0, le=2)]  # sampling knob, not money
    num_ctx: int = Field(ge=2048)


class OpenAICompatibleConfig(Strict):
    base_url: str | None
    model: str | None
    api_key_env: str


class ReplayConfig(Strict):
    cassette_dir: str


class ModelConfig(Strict):
    client: LLMClientKind
    max_intent_retries: int = Field(ge=0, le=2)
    local: LocalModelConfig
    openai_compatible: OpenAICompatibleConfig
    replay: ReplayConfig


def _load_yaml(path: Path) -> Any:
    with path.open(encoding="utf-8") as fh:
        return yaml.safe_load(fh)


def load_policy(path: Path) -> Policy:
    return Policy.model_validate(_load_yaml(path), strict=False)


def load_model_config(path: Path) -> ModelConfig:
    data = _load_yaml(path)
    override = os.environ.get("LLM_CLIENT")
    if override:
        data["client"] = override
    return ModelConfig.model_validate(data, strict=False)


@dataclass(frozen=True)
class Settings:
    db_path: Path
    config_dir: Path
    policy: Policy
    model: ModelConfig
    key_path: Path  # the verifier's sealing key (created on first start, mode 0600)


def load_settings() -> Settings:
    config_dir = Path(os.environ.get("LEDGERGUARD_CONFIG_DIR", "config"))
    db_path = Path(os.environ.get("LEDGERGUARD_DB", "data/ledger.sqlite"))
    return Settings(
        db_path=db_path,
        config_dir=config_dir,
        policy=load_policy(config_dir / "policy.yaml"),
        model=load_model_config(config_dir / "model.yaml"),
        key_path=Path(os.environ.get("LEDGERGUARD_KEY", "data/verifier.key")),
    )
