"""Closed vocabularies shared by the data model, the verifier and the API."""

from __future__ import annotations

from enum import StrEnum


class AccountType(StrEnum):
    BANK = "bank"
    CREDIT_LINE = "credit_line"
    CASH = "cash"


class Direction(StrEnum):
    IN = "in"
    OUT = "out"


class TransactionSource(StrEnum):
    BANK_FEED = "bank_feed"
    MANUAL = "manual"
    INVOICE_SETTLEMENT = "invoice_settlement"


class CounterpartyType(StrEnum):
    CUSTOMER = "customer"
    SUPPLIER = "supplier"
    PAYROLL = "payroll"
    TAX = "tax"
    OTHER = "other"


class RiskBand(StrEnum):
    A = "A"
    B = "B"
    C = "C"


class InvoiceType(StrEnum):
    RECEIVABLE = "receivable"
    PAYABLE = "payable"


class InvoiceStatus(StrEnum):
    DRAFT = "draft"
    SENT = "sent"
    PART_PAID = "part_paid"
    PAID = "paid"
    OVERDUE = "overdue"
    DISPUTED = "disputed"


class PayrollStatus(StrEnum):
    SCHEDULED = "scheduled"
    PAID = "paid"


class ObligationKind(StrEnum):
    TAX = "tax"
    LOAN_REPAYMENT = "loan_repayment"
    LEASE = "lease"
    SUBSCRIPTION = "subscription"


class FinancingKind(StrEnum):
    TERM_LOAN = "term_loan"
    CREDIT_LINE = "credit_line"
    INVOICE_FACTORING = "invoice_factoring"
    SUPPLIER_CREDIT = "supplier_credit"
    MERCHANT_ADVANCE = "merchant_advance"


class ActionKind(StrEnum):
    ANSWER_QUERY = "ANSWER_QUERY"
    DRAFT_FORECAST = "DRAFT_FORECAST"
    RUN_SCENARIO = "RUN_SCENARIO"
    FLAG_ANOMALY = "FLAG_ANOMALY"
    DRAFT_INVOICE = "DRAFT_INVOICE"
    RECORD_PAYMENT = "RECORD_PAYMENT"
    MARK_DISPUTED = "MARK_DISPUTED"
    RANK_FINANCING = "RANK_FINANCING"
    INITIATE_PAYMENT = "INITIATE_PAYMENT"
    ACCEPT_FINANCING_OFFER = "ACCEPT_FINANCING_OFFER"


class IntentStatus(StrEnum):
    PROPOSED = "proposed"
    VERIFIED = "verified"
    REJECTED = "rejected"
    PENDING_APPROVAL = "pending_approval"
    APPROVED = "approved"
    DECLINED = "declined"
    EXECUTED = "executed"
    FAILED = "failed"


class Tier(StrEnum):
    T0 = "T0"
    T1 = "T1"
    T2 = "T2"
    T3 = "T3"


class Actor(StrEnum):
    SYSTEM = "system"
    LLM = "llm"
    OPERATOR = "operator"
    OWNER = "owner"


class Confidence(StrEnum):
    """How firm a projected cash line is (BUILD_SPEC 7.1). Never merged into one number."""

    CONTRACTED = (
        "CONTRACTED"  # issued invoice, signed order, payroll, statutory or lease obligation
    )
    EXPECTED = "EXPECTED"  # forward-dated draft order from the order book, not yet invoiced
    SPECULATIVE = "SPECULATIVE"  # modelled only: seasonal fill beyond the order book
