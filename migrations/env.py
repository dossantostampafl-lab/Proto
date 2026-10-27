from __future__ import annotations

import asyncio
import os
from logging.config import fileConfig

import sqlalchemy as sa
from alembic import context
from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config

from apps.api.app import live_persistence as _live_persistence
from apps.api.app.live_database import LiveBase
from apps.api.app.persistence import Base
from apps.api.app.schema_registry import canonical_metadata
from services.agents.roster import roster_metadata
from services.chatgpt.conversations import metadata as conversation_metadata
from services.events import journal_metadata
from services.oms import ledger_metadata
from services.oms.ledger import ExactDecimal
from services.orchestration import DecisionMemoryBase, OrchestrationBase
from services.promotion import promotion_metadata
from services.risk import control_metadata

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

_database_url = os.environ.get("DATABASE_URL")
if _database_url:
    config.set_main_option("sqlalchemy.url", _database_url.replace("%", "%%"))

target_metadata = [
    conversation_metadata,
    LiveBase.metadata,
    Base.metadata,
    canonical_metadata,
    OrchestrationBase.metadata,
    DecisionMemoryBase.metadata,
    journal_metadata,
    ledger_metadata,
    promotion_metadata,
    roster_metadata,
    control_metadata,
]


def compare_type(_context, _inspected_column, _metadata_column, inspected_type, metadata_type):
    """ExactDecimal is NUMERIC(38, 18) on PostgreSQL and text on SQLite, where a database built
    by migration 0008 keeps NUMERIC: neither is drift. Every other type: Alembic's default."""

    if isinstance(metadata_type, ExactDecimal):
        if isinstance(inspected_type, sa.Numeric):
            return (inspected_type.precision, inspected_type.scale) != (38, 18)
        return not isinstance(inspected_type, sa.String)
    return None


def run_migrations_offline() -> None:
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=compare_type,
    )

    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        compare_type=compare_type,
    )

    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    connectable = async_engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)

    await connectable.dispose()


def run_migrations_online() -> None:
    asyncio.run(run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
