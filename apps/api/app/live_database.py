from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine
from sqlalchemy.orm import DeclarativeBase


class LiveBase(DeclarativeBase):
    """Metadata boundary for the standalone public read-only live monitor."""


def build_live_engine(database_url: str) -> AsyncEngine:
    return create_async_engine(database_url, pool_pre_ping=True)


async def init_live_database(engine: AsyncEngine) -> None:
    async with engine.begin() as connection:
        await connection.run_sync(LiveBase.metadata.create_all)
