"""initial ledger schema

Revision ID: 0001
Revises: (none)

Hand-normalised: plain SQL column types only (money is integer cents,
JSON is canonical text) so this migration never depends on application code.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "account",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("type", sa.String(length=32), nullable=False),
        sa.Column("currency", sa.String(length=3), nullable=False),
        sa.Column("opening_balance", sa.Integer(), nullable=False),
        sa.Column("as_of", sa.Date(), nullable=False),
        sa.Column("credit_limit", sa.Integer(), nullable=True),
        sa.CheckConstraint(
            "type IN ('bank', 'credit_line', 'cash')", name=op.f("ck_account_account_type")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_account")),
    )
    op.create_table(
        "action_intent",
        sa.Column("id", sa.String(length=40), nullable=False),
        sa.Column("conversation_id", sa.String(length=40), nullable=False),
        sa.Column("created_at", sa.String(length=32), nullable=False),
        sa.Column("kind", sa.String(length=32), nullable=False),
        sa.Column("payload", sa.Text(), nullable=False),
        sa.Column("proposer_rationale", sa.Text(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.CheckConstraint(
            "kind IN ('ANSWER_QUERY', 'DRAFT_FORECAST', 'RUN_SCENARIO', 'FLAG_ANOMALY', 'DRAFT_INVOICE', 'RECORD_PAYMENT', 'MARK_DISPUTED', 'RANK_FINANCING', 'INITIATE_PAYMENT', 'ACCEPT_FINANCING_OFFER')",
            name=op.f("ck_action_intent_action_kind"),
        ),
        sa.CheckConstraint(
            "status IN ('proposed', 'verified', 'rejected', 'pending_approval', 'approved', 'declined', 'executed', 'failed')",
            name=op.f("ck_action_intent_intent_status"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_action_intent")),
    )
    with op.batch_alter_table("action_intent", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_action_intent_conversation_id"), ["conversation_id"], unique=False
        )

    op.create_table(
        "audit_event",
        sa.Column("id", sa.String(length=40), nullable=False),
        sa.Column("seq", sa.Integer(), nullable=False),
        sa.Column("occurred_at", sa.String(length=32), nullable=False),
        sa.Column("actor", sa.String(length=32), nullable=False),
        sa.Column("event_type", sa.String(length=64), nullable=False),
        sa.Column("payload", sa.Text(), nullable=False),
        sa.Column("prev_hash", sa.String(length=64), nullable=False),
        sa.Column("hash", sa.String(length=64), nullable=False),
        sa.CheckConstraint(
            "actor IN ('system', 'llm', 'operator', 'owner')", name=op.f("ck_audit_event_actor")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_audit_event")),
        sa.UniqueConstraint("hash", name=op.f("uq_audit_event_hash")),
        sa.UniqueConstraint("seq", name=op.f("uq_audit_event_seq")),
    )
    op.create_table(
        "business",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("legal_name", sa.String(length=200), nullable=False),
        sa.Column("country", sa.String(length=2), nullable=False),
        sa.Column("currency", sa.String(length=3), nullable=False),
        sa.Column("industry_code", sa.String(length=16), nullable=False),
        sa.Column("founded_on", sa.Date(), nullable=False),
        sa.Column("employee_count", sa.Integer(), nullable=False),
        sa.Column("fiscal_year_start", sa.String(length=5), nullable=False),
        sa.Column("data_as_of", sa.Date(), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_business")),
    )
    op.create_table(
        "counterparty",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("type", sa.String(length=32), nullable=False),
        sa.Column("payment_terms_days", sa.Integer(), nullable=False),
        sa.Column("risk_band", sa.String(length=32), nullable=False),
        sa.Column("is_whitelisted", sa.Boolean(), nullable=False),
        sa.CheckConstraint("risk_band IN ('A', 'B', 'C')", name=op.f("ck_counterparty_risk_band")),
        sa.CheckConstraint(
            "type IN ('customer', 'supplier', 'payroll', 'tax', 'other')",
            name=op.f("ck_counterparty_counterparty_type"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_counterparty")),
    )
    op.create_table(
        "financing_product",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("provider", sa.String(length=120), nullable=False),
        sa.Column("name", sa.String(length=160), nullable=False),
        sa.Column("kind", sa.String(length=32), nullable=False),
        sa.Column("min_amount", sa.Integer(), nullable=False),
        sa.Column("max_amount", sa.Integer(), nullable=False),
        sa.Column("nominal_rate", sa.String(length=32), nullable=False),
        sa.Column("fee_structure", sa.Text(), nullable=False),
        sa.Column("term_months", sa.Integer(), nullable=False),
        sa.Column("time_to_funding_days", sa.Integer(), nullable=False),
        sa.Column("eligibility_rules", sa.Text(), nullable=False),
        sa.Column("requires_collateral", sa.Boolean(), nullable=False),
        sa.CheckConstraint(
            "kind IN ('term_loan', 'credit_line', 'invoice_factoring', 'supplier_credit', 'merchant_advance')",
            name=op.f("ck_financing_product_financing_kind"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_financing_product")),
    )
    op.create_table(
        "payroll_run",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("period_start", sa.Date(), nullable=False),
        sa.Column("period_end", sa.Date(), nullable=False),
        sa.Column("pay_date", sa.Date(), nullable=False),
        sa.Column("gross_total", sa.Integer(), nullable=False),
        sa.Column("employer_cost_total", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.CheckConstraint(
            "status IN ('scheduled', 'paid')", name=op.f("ck_payroll_run_payroll_status")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_payroll_run")),
    )
    op.create_table(
        "invoice",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("type", sa.String(length=32), nullable=False),
        sa.Column("counterparty_id", sa.String(length=32), nullable=False),
        sa.Column("issued_on", sa.Date(), nullable=False),
        sa.Column("due_on", sa.Date(), nullable=False),
        sa.Column("gross_amount", sa.Integer(), nullable=False),
        sa.Column("tax_amount", sa.Integer(), nullable=False),
        sa.Column("net_amount", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("paid_amount", sa.Integer(), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.CheckConstraint(
            "status IN ('draft', 'sent', 'part_paid', 'paid', 'overdue', 'disputed')",
            name=op.f("ck_invoice_invoice_status"),
        ),
        sa.CheckConstraint(
            "type IN ('receivable', 'payable')", name=op.f("ck_invoice_invoice_type")
        ),
        sa.CheckConstraint("due_on >= issued_on", name=op.f("ck_invoice_due_after_issue")),
        sa.CheckConstraint(
            "gross_amount = net_amount + tax_amount", name=op.f("ck_invoice_gross_is_net_plus_tax")
        ),
        sa.CheckConstraint(
            "paid_amount >= 0 AND paid_amount <= gross_amount",
            name=op.f("ck_invoice_paid_in_range"),
        ),
        sa.ForeignKeyConstraint(
            ["counterparty_id"],
            ["counterparty.id"],
            name=op.f("fk_invoice_counterparty_id_counterparty"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_invoice")),
    )
    with op.batch_alter_table("invoice", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_invoice_counterparty_id"), ["counterparty_id"], unique=False
        )

    op.create_table(
        "obligation",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("kind", sa.String(length=32), nullable=False),
        sa.Column("due_on", sa.Date(), nullable=False),
        sa.Column("amount", sa.Integer(), nullable=False),
        sa.Column("recurrence_rule", sa.String(length=120), nullable=True),
        sa.Column("counterparty_id", sa.String(length=32), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.CheckConstraint(
            "kind IN ('tax', 'loan_repayment', 'lease', 'subscription')",
            name=op.f("ck_obligation_obligation_kind"),
        ),
        sa.ForeignKeyConstraint(
            ["counterparty_id"],
            ["counterparty.id"],
            name=op.f("fk_obligation_counterparty_id_counterparty"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_obligation")),
    )
    op.create_table(
        "trust_receipt",
        sa.Column("id", sa.String(length=40), nullable=False),
        sa.Column("action_intent_id", sa.String(length=40), nullable=False),
        sa.Column("rows_read", sa.Text(), nullable=False),
        sa.Column("recomputations", sa.Text(), nullable=False),
        sa.Column("rules_evaluated", sa.Text(), nullable=False),
        sa.Column("tier", sa.String(length=32), nullable=False),
        sa.Column("risk_flags", sa.Text(), nullable=False),
        sa.Column("verifier_version", sa.String(length=16), nullable=False),
        sa.Column("created_at", sa.String(length=32), nullable=False),
        sa.CheckConstraint("tier IN ('T0', 'T1', 'T2', 'T3')", name=op.f("ck_trust_receipt_tier")),
        sa.ForeignKeyConstraint(
            ["action_intent_id"],
            ["action_intent.id"],
            name=op.f("fk_trust_receipt_action_intent_id_action_intent"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_trust_receipt")),
    )
    with op.batch_alter_table("trust_receipt", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_trust_receipt_action_intent_id"), ["action_intent_id"], unique=False
        )

    op.create_table(
        "transaction",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("account_id", sa.String(length=32), nullable=False),
        sa.Column("booked_on", sa.Date(), nullable=False),
        sa.Column("value_date", sa.Date(), nullable=False),
        sa.Column("amount", sa.Integer(), nullable=False),
        sa.Column("direction", sa.String(length=32), nullable=False),
        sa.Column("counterparty_id", sa.String(length=32), nullable=False),
        sa.Column("category", sa.String(length=40), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("source", sa.String(length=32), nullable=False),
        sa.Column("reconciled", sa.Boolean(), nullable=False),
        sa.Column("invoice_id", sa.String(length=32), nullable=True),
        sa.Column("payroll_run_id", sa.String(length=32), nullable=True),
        sa.CheckConstraint("direction IN ('in', 'out')", name=op.f("ck_transaction_direction")),
        sa.CheckConstraint(
            "source IN ('bank_feed', 'manual', 'invoice_settlement')",
            name=op.f("ck_transaction_transaction_source"),
        ),
        sa.CheckConstraint("amount > 0", name=op.f("ck_transaction_amount_positive")),
        sa.ForeignKeyConstraint(
            ["account_id"], ["account.id"], name=op.f("fk_transaction_account_id_account")
        ),
        sa.ForeignKeyConstraint(
            ["counterparty_id"],
            ["counterparty.id"],
            name=op.f("fk_transaction_counterparty_id_counterparty"),
        ),
        sa.ForeignKeyConstraint(
            ["invoice_id"], ["invoice.id"], name=op.f("fk_transaction_invoice_id_invoice")
        ),
        sa.ForeignKeyConstraint(
            ["payroll_run_id"],
            ["payroll_run.id"],
            name=op.f("fk_transaction_payroll_run_id_payroll_run"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_transaction")),
    )
    with op.batch_alter_table("transaction", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_transaction_account_id"), ["account_id"], unique=False)
        batch_op.create_index(batch_op.f("ix_transaction_booked_on"), ["booked_on"], unique=False)
        batch_op.create_index(
            batch_op.f("ix_transaction_counterparty_id"), ["counterparty_id"], unique=False
        )


def downgrade() -> None:
    with op.batch_alter_table("transaction", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_transaction_counterparty_id"))
        batch_op.drop_index(batch_op.f("ix_transaction_booked_on"))
        batch_op.drop_index(batch_op.f("ix_transaction_account_id"))

    op.drop_table("transaction")
    with op.batch_alter_table("trust_receipt", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_trust_receipt_action_intent_id"))

    op.drop_table("trust_receipt")
    op.drop_table("obligation")
    with op.batch_alter_table("invoice", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_invoice_counterparty_id"))

    op.drop_table("invoice")
    op.drop_table("payroll_run")
    op.drop_table("financing_product")
    op.drop_table("counterparty")
    op.drop_table("business")
    op.drop_table("audit_event")
    with op.batch_alter_table("action_intent", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_action_intent_conversation_id"))

    op.drop_table("action_intent")
    op.drop_table("account")
