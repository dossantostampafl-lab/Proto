from __future__ import annotations

from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncEngine

from services.market_data import PersistedLiveTickPage

from .live_database import build_live_engine, init_live_database
from .live_monitor import LiveCryptoMonitor
from .live_persistence import AsyncSqlLiveTickJournal
from .settings import Settings


class LiveDurabilityRuntime:
    """Owns the optional SQL lifecycle for public read-only live history."""

    def __init__(self) -> None:
        self._engine: AsyncEngine | None = None
        self._journal: AsyncSqlLiveTickJournal | None = None

    @property
    def running(self) -> bool:
        return self._engine is not None

    async def start(self, *, monitor: LiveCryptoMonitor, settings: Settings) -> None:
        if self.running:
            return
        if not settings.persistence_enabled:
            monitor.configure_persistence(None, required=False)
            return

        engine = build_live_engine(settings.database_url)
        journal = AsyncSqlLiveTickJournal(
            engine,
            retention_seconds=settings.live_history_retention_seconds,
            prune_every_writes=settings.live_history_prune_every_writes,
        )
        try:
            if settings.live_database_auto_create:
                await init_live_database(engine)
            await journal.prune_expired()
        except Exception:
            await engine.dispose()
            monitor.configure_persistence(None, required=True)
            raise

        self._engine = engine
        self._journal = journal
        monitor.configure_persistence(journal, required=True)

    async def history_page(
        self,
        *,
        symbol: str,
        limit: int,
        cursor: str | None = None,
        start_at: datetime | None = None,
        end_at: datetime | None = None,
    ) -> PersistedLiveTickPage | None:
        journal = self._journal
        if journal is None:
            return None
        return await journal.list_page(
            symbol=symbol,
            limit=limit,
            cursor=cursor,
            start_at=start_at,
            end_at=end_at,
        )

    async def stop(self, *, monitor: LiveCryptoMonitor) -> None:
        engine = self._engine
        self._engine = None
        self._journal = None
        monitor.configure_persistence(None, required=False)
        if engine is not None:
            await engine.dispose()


live_durability = LiveDurabilityRuntime()
