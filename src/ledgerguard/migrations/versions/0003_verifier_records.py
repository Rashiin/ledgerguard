"""verifier records: rejected intents, sealed pending actions, full receipts

Revision ID: 0003
Revises: 0002

SQLite cannot alter column nullability in place, so both tables are rebuilt.
The new definitions are written out explicitly (create, copy, drop, rename)
rather than with Alembic batch mode: batch mode reflects the old table, and
reflected constraint order is not stable between runs. That would break the
byte-identical seed.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None

KINDS = (
    "ANSWER_QUERY",
    "DRAFT_FORECAST",
    "RUN_SCENARIO",
    "FLAG_ANOMALY",
    "DRAFT_INVOICE",
    "RECORD_PAYMENT",
    "MARK_DISPUTED",
    "RANK_FINANCING",
    "INITIATE_PAYMENT",
    "ACCEPT_FINANCING_OFFER",
)
STATUSES = (
    "proposed",
    "verified",
    "rejected",
    "pending_approval",
    "approved",
    "declined",
    "executed",
    "failed",
)


def _in(column: str, values: tuple[str, ...]) -> str:
    return f"{column} IN ({', '.join(repr(v) for v in values)})"


def upgrade() -> None:
    op.create_table(
        "action_intent_new",
        sa.Column("id", sa.String(length=40), nullable=False),
        sa.Column("conversation_id", sa.String(length=40), nullable=False),
        sa.Column("created_at", sa.String(length=32), nullable=False),
        sa.Column("kind", sa.String(length=32), nullable=True),
        sa.Column("payload", sa.Text(), nullable=True),
        sa.Column("proposer_rationale", sa.Text(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("raw_output", sa.Text(), nullable=True),
        sa.Column("sealed_action", sa.Text(), nullable=True),
        sa.CheckConstraint(_in("kind", KINDS), name=op.f("ck_action_intent_action_kind")),
        sa.CheckConstraint(_in("status", STATUSES), name=op.f("ck_action_intent_intent_status")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_action_intent")),
    )
    op.execute(
        "INSERT INTO action_intent_new (id, conversation_id, created_at, kind, payload, "
        "proposer_rationale, status) SELECT id, conversation_id, created_at, kind, payload, "
        "proposer_rationale, status FROM action_intent"
    )
    op.create_table(
        "trust_receipt_new",
        sa.Column("id", sa.String(length=40), nullable=False),
        sa.Column("action_intent_id", sa.String(length=40), nullable=False),
        sa.Column("rows_read", sa.Text(), nullable=False),
        sa.Column("recomputations", sa.Text(), nullable=False),
        sa.Column("rules_evaluated", sa.Text(), nullable=False),
        sa.Column("tier", sa.String(length=32), nullable=True),
        sa.Column("risk_flags", sa.Text(), nullable=False),
        sa.Column("verifier_version", sa.String(length=16), nullable=False),
        sa.Column("created_at", sa.String(length=32), nullable=False),
        sa.Column("outcome", sa.String(length=16), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("audit_seq", sa.Integer(), nullable=True),
        sa.CheckConstraint(
            _in("tier", ("T0", "T1", "T2", "T3")), name=op.f("ck_trust_receipt_tier")
        ),
        sa.ForeignKeyConstraint(
            ["action_intent_id"],
            ["action_intent.id"],
            name=op.f("fk_trust_receipt_action_intent_id_action_intent"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_trust_receipt")),
    )
    op.execute(
        "INSERT INTO trust_receipt_new (id, action_intent_id, rows_read, recomputations, "
        "rules_evaluated, tier, risk_flags, verifier_version, created_at, outcome, body) "
        "SELECT id, action_intent_id, rows_read, recomputations, rules_evaluated, tier, "
        "risk_flags, verifier_version, created_at, 'verified', '{}' FROM trust_receipt"
    )
    op.drop_index("ix_trust_receipt_action_intent_id", table_name="trust_receipt")
    op.drop_table("trust_receipt")
    op.drop_index("ix_action_intent_conversation_id", table_name="action_intent")
    op.drop_table("action_intent")
    op.rename_table("action_intent_new", "action_intent")
    op.rename_table("trust_receipt_new", "trust_receipt")
    op.create_index("ix_action_intent_conversation_id", "action_intent", ["conversation_id"])
    op.create_index("ix_trust_receipt_action_intent_id", "trust_receipt", ["action_intent_id"])


def downgrade() -> None:
    raise NotImplementedError("0003 is not reversible; rebuild with scripts/seed.py")
