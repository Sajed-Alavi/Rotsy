"""Alembic env.

Runs migrations synchronously. We import the app settings to get
``DATABASE_URL`` and convert the async ``postgresql+asyncpg://`` form to the
synchronous ``postgresql+psycopg://`` form so Alembic can use its standard
sync engine. (psycopg3 is shipped via SQLAlchemy 2.x without an extra dep
because Alembic/SQLAlchemy use the DBAPI directly; if psycopg is unavailable
we fall back to the plain ``postgresql://`` driver.)
"""

from __future__ import annotations

import os
import sys
from logging.config import fileConfig
from pathlib import Path

from alembic import context
from sqlalchemy import engine_from_config, pool

# Make ``app`` importable when alembic runs from the backend/ dir.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import get_settings  # noqa: E402
from app.db.base import Base  # noqa: E402
from app.models import user  # noqa: F401,E402  (registers tables on Base.metadata)

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# Resolve DB URL from settings (convert async -> sync for alembic).
_settings = get_settings()
_sync_url = _settings.DATABASE_URL.replace(
    "postgresql+asyncpg://", "postgresql+psycopg://"
).replace("postgresql://", "postgresql+psycopg://")
config.set_main_option("sqlalchemy.url", _sync_url)

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
