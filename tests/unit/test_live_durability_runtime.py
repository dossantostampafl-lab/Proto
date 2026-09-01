from collections.abc import AsyncIterator
from datetime import UTC, datetime
from pathlib import Path

import pytest

from apps.api.app.live_durability import LiveDurabilityRuntime
from apps.api.app.live_monitor import LiveCryptoMonitor
from apps.api.app.settings import Settings
from services.market_data import LiveTickJournalError, MarketTick, PublicFeedHealth


class StaticAdapter:
    symbols = ("BTC", "ETH", "SOL")

    def health(self) -> PublicFeedHealth:
        now = datetime.now(UTC)
        return PublicFeedHealth(
            connected=True,
            connection_generation=1,
            connection_attempts=1,
            reconnect_count=0,
            frames_received=1,
            ticks_emitted=1,
            parse_error_count=0,
            connected_since=now,
            last_message_at=now,
            last_tick_at=now,
            last_error=None,
        )

    async def stream(self) -> AsyncIterator[MarketTick]:
        if False:
            yield _tick()


def _tick() -> MarketTick:
    return MarketTick(
        timestamp=datetime.now(UTC),
        venue="coinbase-public",
        symbol="BTC",
        bid=60_000.0,
        ask=60_001.0,
        last=60_000.5,
        volume=100.0,
        bid_size=1.0,
        ask_size=1.0,
        sequence=101,
    )


@pytest.mark.asyncio
async def test_live_durability_runtime_persists_and_recovers_history(tmp_path: Path) -> None:
    database_path = tmp_path / "live-history.db"
    settings = Settings(
        live_persistence_enabled=True,
        database_url=f"sqlite+aiosqlite:///{database_path}",
        live_history_retention_seconds=3600,
        live_history_prune_every_writes=100,
    )
    monitor = LiveCryptoMonitor(StaticAdapter())
    runtime = LiveDurabilityRuntime()

    await runtime.start(monitor=monitor, settings=settings)
    try:
        persistence = monitor.persistence_status()
        assert persistence["configured"] is True
        assert persistence["required"] is True
        assert persistence["healthy"] is True

        assert await monitor.ingest_tick(_tick()) is True
        history = await monitor.persisted_history("BTC", limit=10)
        assert history is not None
        assert len(history) == 1
        assert history[0]["sequence"] == 101
        assert history[0]["source"] == "PUBLIC_READ_ONLY_PERSISTED"
    finally:
        await runtime.stop(monitor=monitor)

    persistence = monitor.persistence_status()
    assert persistence["configured"] is False
    assert persistence["required"] is False

    restarted_monitor = LiveCryptoMonitor(StaticAdapter())
    restarted_runtime = LiveDurabilityRuntime()
    await restarted_runtime.start(monitor=restarted_monitor, settings=settings)
    try:
        recovered = await restarted_monitor.persisted_history("BTC", limit=10)
        assert recovered is not None
        assert [row["sequence"] for row in recovered] == [101]
        assert restarted_monitor.snapshot("BTC") is None
    finally:
        await restarted_runtime.stop(monitor=restarted_monitor)


@pytest.mark.asyncio
async def test_live_persistence_is_independent_from_general_persistence() -> None:
    monitor = LiveCryptoMonitor(StaticAdapter())
    runtime = LiveDurabilityRuntime()
    settings = Settings(persistence_enabled=True, live_persistence_enabled=False)

    await runtime.start(monitor=monitor, settings=settings)

    assert runtime.running is False
    persistence = monitor.persistence_status()
    assert persistence["configured"] is False
    assert persistence["required"] is False


@pytest.mark.asyncio
async def test_migration_managed_runtime_fails_closed_when_schema_is_missing(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "missing-live-schema.db"
    settings = Settings(
        live_persistence_enabled=True,
        live_database_auto_create=False,
        database_url=f"sqlite+aiosqlite:///{database_path}",
        live_history_retention_seconds=3600,
    )
    monitor = LiveCryptoMonitor(StaticAdapter())
    runtime = LiveDurabilityRuntime()

    with pytest.raises(LiveTickJournalError):
        await runtime.start(monitor=monitor, settings=settings)

    assert runtime.running is False
    persistence = monitor.persistence_status()
    assert persistence["configured"] is False
    assert persistence["required"] is True
    assert persistence["healthy"] is False
