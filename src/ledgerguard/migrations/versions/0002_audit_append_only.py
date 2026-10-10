"""audit log is append-only

Revision ID: 0002
Revises: 0001

SQLite triggers refuse UPDATE and DELETE on audit_event, so even the
Executor's writable connection can only append. Someone with direct access
to the database file can still drop the triggers; the hash chain is what
makes that kind of tampering detectable.
"""

from __future__ import annotations

from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "CREATE TRIGGER audit_event_no_update BEFORE UPDATE ON audit_event "
        "BEGIN SELECT RAISE(ABORT, 'audit_event is append-only'); END"
    )
    op.execute(
        "CREATE TRIGGER audit_event_no_delete BEFORE DELETE ON audit_event "
        "BEGIN SELECT RAISE(ABORT, 'audit_event is append-only'); END"
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER audit_event_no_delete")
    op.execute("DROP TRIGGER audit_event_no_update")
