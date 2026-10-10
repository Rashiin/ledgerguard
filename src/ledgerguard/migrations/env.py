"""Alembic environment. The database URL is supplied by ledgerguard.provisioning."""

from __future__ import annotations

from alembic import context
from sqlalchemy import create_engine

from ledgerguard.db.models import Base

config = context.config
target_metadata = Base.metadata


def run_migrations_online() -> None:
    url = config.get_main_option("sqlalchemy.url")
    if not url:
        raise RuntimeError("sqlalchemy.url must be set by the caller")
    engine = create_engine(url)
    with engine.connect() as connection:
        context.configure(
            connection=connection, target_metadata=target_metadata, render_as_batch=True
        )
        with context.begin_transaction():
            context.run_migrations()
    engine.dispose()


if context.is_offline_mode():
    raise RuntimeError("offline migrations are not supported")
run_migrations_online()
