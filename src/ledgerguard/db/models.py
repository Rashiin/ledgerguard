"""SQLAlchemy models for the sandbox ledger, the verifier records and the audit log.

Columns follow the required schema. Additions beyond it are marked
``# added:`` and explained in docs/DECISIONS.md.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any

from sqlalchemy import CheckConstraint, Date, ForeignKey, MetaData, String, Text
from sqlalchemy import Enum as SAEnum
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from ledgerguard.db.types import CanonicalJSON, DecimalText, Money, UTCDateTime
from ledgerguard.domain.enums import (
    AccountType,
    ActionKind,
    Actor,
    CounterpartyType,
    Direction,
    FinancingKind,
    IntentStatus,
    InvoiceStatus,
    InvoiceType,
    ObligationKind,
    PayrollStatus,
    RiskBand,
    Tier,
    TransactionSource,
)

NAMING = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


def enum_column(enum: type[StrEnum], name: str) -> SAEnum:
    """Store enum *values* as constrained text so the database rejects unknown members."""
    return SAEnum(
        enum,
        name=name,
        native_enum=False,
        create_constraint=True,
        length=32,
        values_callable=lambda members: [m.value for m in members],
        validate_strings=True,
    )


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING)


class Business(Base):
    __tablename__ = "business"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    legal_name: Mapped[str] = mapped_column(String(200))
    country: Mapped[str] = mapped_column(String(2))
    currency: Mapped[str] = mapped_column(String(3))
    industry_code: Mapped[str] = mapped_column(String(16))
    founded_on: Mapped[date] = mapped_column(Date)
    employee_count: Mapped[int]
    fiscal_year_start: Mapped[str] = mapped_column(String(5))  # "MM-DD"
    # added: the ledger's "today". Every projection is relative to this date,
    # never to the wall clock, so the demo is reproducible on any day.
    data_as_of: Mapped[date] = mapped_column(Date)


class Account(Base):
    __tablename__ = "account"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    name: Mapped[str] = mapped_column(String(120))
    type: Mapped[AccountType] = mapped_column(enum_column(AccountType, "account_type"))
    currency: Mapped[str] = mapped_column(String(3))
    # Balance at the start of the ledger history, on ``as_of``.
    opening_balance: Mapped[Decimal] = mapped_column(Money)
    as_of: Mapped[date] = mapped_column(Date)
    # added: facility size for credit lines; NULL for bank and cash accounts.
    credit_limit: Mapped[Decimal | None] = mapped_column(Money, nullable=True)


class Counterparty(Base):
    __tablename__ = "counterparty"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    type: Mapped[CounterpartyType] = mapped_column(
        enum_column(CounterpartyType, "counterparty_type")
    )
    payment_terms_days: Mapped[int]
    risk_band: Mapped[RiskBand] = mapped_column(enum_column(RiskBand, "risk_band"))
    is_whitelisted: Mapped[bool]


class Invoice(Base):
    __tablename__ = "invoice"
    __table_args__ = (
        CheckConstraint("gross_amount = net_amount + tax_amount", name="gross_is_net_plus_tax"),
        CheckConstraint("paid_amount >= 0 AND paid_amount <= gross_amount", name="paid_in_range"),
        CheckConstraint("due_on >= issued_on", name="due_after_issue"),
    )

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    type: Mapped[InvoiceType] = mapped_column(enum_column(InvoiceType, "invoice_type"))
    counterparty_id: Mapped[str] = mapped_column(ForeignKey("counterparty.id"), index=True)
    issued_on: Mapped[date] = mapped_column(Date)
    due_on: Mapped[date] = mapped_column(Date)
    gross_amount: Mapped[Decimal] = mapped_column(Money)
    tax_amount: Mapped[Decimal] = mapped_column(Money)
    net_amount: Mapped[Decimal] = mapped_column(Money)
    status: Mapped[InvoiceStatus] = mapped_column(enum_column(InvoiceStatus, "invoice_status"))
    paid_amount: Mapped[Decimal] = mapped_column(Money)
    # added: free text. Untrusted content; the injection fixtures live here.
    description: Mapped[str] = mapped_column(Text)


class PayrollRun(Base):
    __tablename__ = "payroll_run"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    period_start: Mapped[date] = mapped_column(Date)
    period_end: Mapped[date] = mapped_column(Date)
    pay_date: Mapped[date] = mapped_column(Date)
    gross_total: Mapped[Decimal] = mapped_column(Money)
    employer_cost_total: Mapped[Decimal] = mapped_column(Money)
    status: Mapped[PayrollStatus] = mapped_column(enum_column(PayrollStatus, "payroll_status"))


class Transaction(Base):
    __tablename__ = "transaction"
    __table_args__ = (CheckConstraint("amount > 0", name="amount_positive"),)

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    account_id: Mapped[str] = mapped_column(ForeignKey("account.id"), index=True)
    booked_on: Mapped[date] = mapped_column(Date, index=True)
    value_date: Mapped[date] = mapped_column(Date)
    amount: Mapped[Decimal] = mapped_column(Money)
    direction: Mapped[Direction] = mapped_column(enum_column(Direction, "direction"))
    counterparty_id: Mapped[str] = mapped_column(ForeignKey("counterparty.id"), index=True)
    category: Mapped[str] = mapped_column(String(40))
    description: Mapped[str] = mapped_column(Text)
    source: Mapped[TransactionSource] = mapped_column(
        enum_column(TransactionSource, "transaction_source")
    )
    reconciled: Mapped[bool]
    # added: settlement links, so recomputation can walk from a payment to its source.
    invoice_id: Mapped[str | None] = mapped_column(ForeignKey("invoice.id"), nullable=True)
    payroll_run_id: Mapped[str | None] = mapped_column(ForeignKey("payroll_run.id"), nullable=True)


class Obligation(Base):
    __tablename__ = "obligation"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    kind: Mapped[ObligationKind] = mapped_column(enum_column(ObligationKind, "obligation_kind"))
    due_on: Mapped[date] = mapped_column(Date)
    amount: Mapped[Decimal] = mapped_column(Money)
    # RFC 5545 RRULE of the series this occurrence belongs to; NULL for one-offs.
    recurrence_rule: Mapped[str | None] = mapped_column(String(120), nullable=True)
    counterparty_id: Mapped[str] = mapped_column(ForeignKey("counterparty.id"))
    # added: what the obligation is, for the owner reading a forecast.
    description: Mapped[str] = mapped_column(Text)


class FinancingProduct(Base):
    __tablename__ = "financing_product"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    provider: Mapped[str] = mapped_column(String(120))
    # added: product display name.
    name: Mapped[str] = mapped_column(String(160))
    kind: Mapped[FinancingKind] = mapped_column(enum_column(FinancingKind, "financing_kind"))
    min_amount: Mapped[Decimal] = mapped_column(Money)
    max_amount: Mapped[Decimal] = mapped_column(Money)
    nominal_rate: Mapped[Decimal] = mapped_column(DecimalText)  # annual, as a fraction
    fee_structure: Mapped[dict[str, Any]] = mapped_column(CanonicalJSON)
    term_months: Mapped[int]
    time_to_funding_days: Mapped[int]
    eligibility_rules: Mapped[dict[str, Any]] = mapped_column(CanonicalJSON)
    requires_collateral: Mapped[bool]


class ActionIntentRecord(Base):
    __tablename__ = "action_intent"

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    conversation_id: Mapped[str] = mapped_column(String(40), index=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime)
    # NULL when the proposer's output was rejected before its kind could be read.
    kind: Mapped[ActionKind | None] = mapped_column(
        enum_column(ActionKind, "action_kind"), nullable=True
    )
    payload: Mapped[dict[str, Any] | None] = mapped_column(CanonicalJSON, nullable=True)
    proposer_rationale: Mapped[str] = mapped_column(Text)
    status: Mapped[IntentStatus] = mapped_column(enum_column(IntentStatus, "intent_status"))
    # added: the proposer's raw output, kept verbatim up to 8,000 characters.
    raw_output: Mapped[str | None] = mapped_column(Text, nullable=True)
    # added: the sealed VerifiedAction awaiting approval, with its seal. The
    # Executor re-checks the seal when it is approved, so edits here are caught.
    sealed_action: Mapped[dict[str, Any] | None] = mapped_column(CanonicalJSON, nullable=True)


class TrustReceiptRecord(Base):
    __tablename__ = "trust_receipt"

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    action_intent_id: Mapped[str] = mapped_column(ForeignKey("action_intent.id"), index=True)
    rows_read: Mapped[list[Any]] = mapped_column(CanonicalJSON)
    recomputations: Mapped[list[Any]] = mapped_column(CanonicalJSON)
    rules_evaluated: Mapped[list[Any]] = mapped_column(CanonicalJSON)
    # NULL for intents rejected before a tier could be assigned.
    tier: Mapped[Tier | None] = mapped_column(enum_column(Tier, "tier"), nullable=True)
    risk_flags: Mapped[list[Any]] = mapped_column(CanonicalJSON)
    verifier_version: Mapped[str] = mapped_column(String(16))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime)
    # added: verified | blocked | rejected
    outcome: Mapped[str] = mapped_column(String(16))
    # added: the complete receipt document, exactly as served and exported.
    body: Mapped[dict[str, Any]] = mapped_column(CanonicalJSON)
    # added: the audit event that recorded this receipt.
    audit_seq: Mapped[int | None] = mapped_column(nullable=True)


class AuditEvent(Base):
    __tablename__ = "audit_event"

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    seq: Mapped[int] = mapped_column(unique=True)
    occurred_at: Mapped[datetime] = mapped_column(UTCDateTime)
    actor: Mapped[Actor] = mapped_column(enum_column(Actor, "actor"))
    event_type: Mapped[str] = mapped_column(String(64))
    payload: Mapped[dict[str, Any]] = mapped_column(CanonicalJSON)
    prev_hash: Mapped[str] = mapped_column(String(64))
    hash: Mapped[str] = mapped_column(String(64), unique=True)
