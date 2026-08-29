from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncEngine

from .live_monitor import LiveCryptoMonitor
from .live_persistence import AsyncSqlLiveTickJournal
from .persistence import build_engine, init_database
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

        engine = build_engine(settings.database_url)
        journal = AsyncSqlLiveTickJournal(
            engine,
            retention_seconds=settings.live_history_retention_seconds,
            prune_every_writes=settings.live_history_prune_every_writes,
        )
        try:
            await init_database(engine)
            await journal.prune_expired()
        except Exception:
            await engine.dispose()
            monitor.configure_persistence(None, required=True)
            raise

        self._engine = engine
        self._journal = journal
        monitor.configure_persistence(journal, required=True)

    async def stop(self, *, monitor: LiveCryptoMonitor) -> None:
        engine = self._engine
        self._engine = None
        self._journal = None
        monitor.configure_persistence(None, required=False)
        if engine is not None:
            await engine.dispose()


live_durability = LiveDurabilityRuntime()
