from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine, create_async_engine
from sqlalchemy.orm import DeclarativeBase


class LiveBase(DeclarativeBase):
    """Metadata boundary for the standalone public read-only live monitor."""


def build_live_engine(database_url: str) -> AsyncEngine:
    if database_url.startswith("postgresql://"):
        database_url = database_url.replace("postgresql://", "postgresql+asyncpg://", 1)
    return create_async_engine(database_url, pool_pre_ping=True)


async def widen_tick_sequence(connection: AsyncConnection) -> bool:
    """Widen live_market_ticks.sequence to BIGINT on a table created when it was INTEGER.

    create_all never alters an existing table, and Binance sequences (epoch milliseconds)
    overflow a 32-bit INTEGER: every Binance tick failed to persist and was dropped.
    """

    if connection.dialect.name != "postgresql":
        return False  # SQLite integers are already 64-bit
    found = await connection.execute(
        text(
            "SELECT data_type FROM information_schema.columns "
            "WHERE table_name = 'live_market_ticks' AND column_name = 'sequence'"
        )
    )
    if found.scalar() != "integer":
        return False
    await connection.execute(
        text("ALTER TABLE live_market_ticks ALTER COLUMN sequence TYPE BIGINT")
    )
    return True


async def init_live_database(engine: AsyncEngine) -> None:
    async with engine.begin() as connection:
        await connection.run_sync(LiveBase.metadata.create_all)
        await widen_tick_sequence(connection)
