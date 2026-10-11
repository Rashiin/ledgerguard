"""The ActionIntent contract, the proposer's only way to make anything happen.

Parsing is strict:

* input must be JSON text;
* unknown fields are errors;
* types are not coerced;
* money must be a quoted decimal string. A JSON number is refused, because
  it has already passed through binary floating point;
* the action kind and the derivation formula come from closed sets.

A ``tier`` key at the top level is removed before parsing and recorded. The
model does not choose tiers, and trying to is not a reason to reject the
whole action. Any other unknown key rejects the intent.

Payment payloads carry no amounts. The verifier computes what is owed from
the ledger.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from enum import StrEnum
from typing import Annotated, Any, Literal

from pydantic import (
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    ValidationError,
    model_validator,
)

from ledgerguard.domain.enums import ActionKind, Confidence
from ledgerguard.money import to_money

MAX_CLAIMS = 40
MAX_TEXT = 2000
MAX_RAW_BYTES = 64_000


def _figure(value: Any) -> Decimal:
    """Claim figures: quoted decimal strings only. Ratios may carry more places."""
    if isinstance(value, bool) or not isinstance(value, str):
        raise ValueError('figures must be quoted decimal strings, e.g. "18400.00"')
    try:
        number = Decimal(value.strip())
    except ArithmeticError as exc:
        raise ValueError(f"not a decimal: {value!r}") from exc
    if not number.is_finite():
        raise ValueError("figures must be finite")
    return number


Money = Annotated[Decimal, BeforeValidator(to_money)]
Figure = Annotated[Decimal, BeforeValidator(_figure)]
RowId = Annotated[str, Field(pattern=r"^[a-z]{2,4}_[A-Za-z0-9_]{1,40}$")]
Text = Annotated[str, Field(min_length=1, max_length=MAX_TEXT)]


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)


# --------------------------------------------------------------------------- claims


class Formula(StrEnum):
    """Closed set of derivations the verifier knows how to recompute."""

    ROW_FIELD = "ROW_FIELD"  # one row's monetary field, params.field
    INVOICE_OUTSTANDING = "INVOICE_OUTSTANDING"  # sum of gross minus paid over invoices
    SUM_TRANSACTIONS = "SUM_TRANSACTIONS"  # sum of transaction amounts
    ACCOUNT_BALANCE = "ACCOUNT_BALANCE"  # one account's balance at the as-of date
    CASH_ON_HAND = "CASH_ON_HAND"  # sum of cited bank/cash account balances
    PROJECTED_CLOSING = "PROJECTED_CLOSING"  # params.week, params.band
    PROJECTED_INFLOW = "PROJECTED_INFLOW"  # params.week, params.confidence
    PROJECTED_OUTFLOW = "PROJECTED_OUTFLOW"  # params.week, params.confidence
    REVENUE_SHARE = "REVENUE_SHARE"  # one counterparty, % of trailing-12-month net revenue
    METRIC = "METRIC"  # params.name, one of the financial analysis metrics
    SCENARIO_CLOSING = "SCENARIO_CLOSING"  # params.scenario, params.week, params.band
    FINANCING_TOTAL_COST = "FINANCING_TOTAL_COST"  # one product, params.amount and params.days


class Derivation(Strict):
    formula: Formula
    operands: list[RowId] = Field(min_length=1, max_length=200)
    params: dict[str, str] = Field(default_factory=dict, max_length=8)


FactId = Annotated[str, Field(pattern=r"^[a-z][A-Za-z0-9_.]{1,60}$")]


class Claim(Strict):
    statement: Text
    # A fact a tool provided. The verifier recomputes it from the ledger and uses
    # its derivation and rows, so the model does not build either itself.
    fact_id: FactId | None = None
    figure: Figure | None = None
    derivation: Derivation | None = None
    # A claim with neither a fact nor any rows is not a schema error. The
    # provenance stage removes it visibly.
    source_row_ids: list[RowId] = Field(default_factory=list, max_length=200)

    @model_validator(mode="after")
    def _one_source_of_derivation(self) -> Claim:
        if self.fact_id is not None and self.derivation is not None:
            raise ValueError("a claim cites a fact or gives a derivation, not both")
        return self


# --------------------------------------------------------------------------- payloads


class AnswerQuery(Strict):
    topic: Text


class DraftForecast(Strict):
    weeks: int = Field(ge=1, le=13)


class CustomerPaysLate(Strict):
    type: Literal["customer_pays_late"]
    counterparty_id: RowId
    days: int = Field(ge=1, le=180)


class DelayPayable(Strict):
    type: Literal["delay_payable"]
    invoice_id: RowId
    days: int = Field(ge=1, le=180)


class DrawCreditLine(Strict):
    type: Literal["draw_credit_line"]
    amount: Money
    on: date


class ContractedInflowsOnly(Strict):
    """Stress view: contracted inflows against all outflows."""

    type: Literal["contracted_inflows_only"]


Scenario = Annotated[
    CustomerPaysLate | DelayPayable | DrawCreditLine | ContractedInflowsOnly,
    Field(discriminator="type"),
]


class RunScenario(Strict):
    scenario: Scenario


class FlagAnomaly(Strict):
    transaction_ids: list[RowId] = Field(min_length=1, max_length=50)


class DraftInvoice(Strict):
    counterparty_id: RowId
    net_amount: Money  # the owner's stated amount; escalates tier when large
    description: Text
    due_in_days: int = Field(ge=0, le=180)


class RecordPayment(Strict):
    invoice_id: RowId
    # Omitted means "paid in full"; the verifier computes the outstanding amount.
    amount: Money | None = None
    received_on: date | None = None


class MarkDisputed(Strict):
    invoice_id: RowId
    reason: Text


class RankFinancing(Strict):
    needed_by_week: int | None = Field(default=None, ge=1, le=13)


class InitiatePayment(Strict):
    invoice_ids: list[RowId] = Field(min_length=1, max_length=200)
    pay_on: date | None = None


class AcceptFinancingOffer(Strict):
    product_id: RowId
    amount: Money


Payload = (
    AnswerQuery
    | DraftForecast
    | RunScenario
    | FlagAnomaly
    | DraftInvoice
    | RecordPayment
    | MarkDisputed
    | RankFinancing
    | InitiatePayment
    | AcceptFinancingOffer
)

PAYLOADS: dict[ActionKind, type[Payload]] = {
    ActionKind.ANSWER_QUERY: AnswerQuery,
    ActionKind.DRAFT_FORECAST: DraftForecast,
    ActionKind.RUN_SCENARIO: RunScenario,
    ActionKind.FLAG_ANOMALY: FlagAnomaly,
    ActionKind.DRAFT_INVOICE: DraftInvoice,
    ActionKind.RECORD_PAYMENT: RecordPayment,
    ActionKind.MARK_DISPUTED: MarkDisputed,
    ActionKind.RANK_FINANCING: RankFinancing,
    ActionKind.INITIATE_PAYMENT: InitiatePayment,
    ActionKind.ACCEPT_FINANCING_OFFER: AcceptFinancingOffer,
}


class ActionIntent(Strict):
    kind: ActionKind
    payload: Payload
    claims: list[Claim] = Field(max_length=MAX_CLAIMS)
    rationale: Text

    @model_validator(mode="after")
    def _payload_matches_kind(self) -> ActionIntent:
        if type(self.payload) is not PAYLOADS[self.kind]:
            raise ValueError(f"payload is not a {self.kind.value} payload")
        return self


class _Envelope(Strict):
    """Everything except the payload, which is validated against its kind afterwards."""

    kind: ActionKind
    payload: dict[str, Any]
    claims: list[Claim] = Field(max_length=MAX_CLAIMS)
    rationale: Text


# --------------------------------------------------------------------------- parsing


@dataclass(frozen=True)
class SchemaError:
    location: str
    message: str


@dataclass(frozen=True)
class ParseResult:
    intent: ActionIntent | None
    errors: tuple[SchemaError, ...]
    ignored_tier: str | None  # what the model claimed, if it tried


def _reject_float(text: str) -> Any:
    raise ValueError(f"bare JSON number {text} is not allowed; quote decimals as strings")


def parse_intent(raw: str | bytes) -> ParseResult:
    """Stage 1. Never repairs: either a valid intent or a list of errors."""
    if isinstance(raw, bytes):
        try:
            raw = raw.decode("utf-8")
        except UnicodeDecodeError:
            return ParseResult(None, (SchemaError("$", "output is not UTF-8 text"),), None)
    if len(raw.encode("utf-8")) > MAX_RAW_BYTES:
        return ParseResult(None, (SchemaError("$", "output too large"),), None)
    try:
        data = json.loads(raw, parse_float=_reject_float)
    except ValueError as exc:
        return ParseResult(None, (SchemaError("$", f"not valid JSON: {exc}"),), None)
    if not isinstance(data, dict):
        return ParseResult(None, (SchemaError("$", "top level must be a JSON object"),), None)
    ignored = data.pop("tier", None)
    ignored_tier = None if ignored is None else str(ignored)
    try:
        envelope = _Envelope.model_validate_json(json.dumps(data))
        payload = PAYLOADS[envelope.kind].model_validate_json(json.dumps(envelope.payload))
        intent = ActionIntent(
            kind=envelope.kind,
            payload=payload,
            claims=envelope.claims,
            rationale=envelope.rationale,
        )
    except ValidationError as exc:
        prefix = "" if exc.title == "_Envelope" else "payload."
        errors = tuple(
            SchemaError(prefix + (".".join(str(p) for p in err["loc"]) or "$"), err["msg"])
            for err in exc.errors()
        )
        return ParseResult(None, errors, ignored_tier)
    return ParseResult(intent, (), ignored_tier)


__all__ = [
    "ActionIntent",
    "Claim",
    "Confidence",
    "Derivation",
    "Formula",
    "ParseResult",
    "SchemaError",
    "parse_intent",
]
