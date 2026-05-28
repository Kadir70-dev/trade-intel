"""Alembic runtime environment.

* DB URL is sourced from `trade_intel.config.get_settings()` (env-driven).
* Importing `trade_intel.db.models` registers every table on `Base.metadata`.
* SQLite gets `render_as_batch=True` so future ALTER TABLE migrations work
  via batch operation mode (SQLite cannot ALTER COLUMN natively).
"""

from __future__ import annotations

from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

import trade_intel.db.models  # noqa: F401 — register tables on Base.metadata
from trade_intel.config import get_settings
from trade_intel.db.base import Base

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

_settings = get_settings()
_db_url = _settings.database_url
config.set_main_option("sqlalchemy.url", _db_url)

target_metadata = Base.metadata


def _is_sqlite() -> bool:
    return _db_url.startswith("sqlite")


def run_migrations_offline() -> None:
    """Emit SQL without a live DB connection (--sql)."""
    context.configure(
        url=_db_url,
        target_metadata=target_metadata,
        literal_binds=True,
        compare_type=True,
        compare_server_default=True,
        render_as_batch=_is_sqlite(),
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations against a live connection."""
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
            compare_server_default=True,
            render_as_batch=_is_sqlite(),
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
