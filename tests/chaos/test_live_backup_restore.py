from __future__ import annotations

import shutil
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from pathlib import Path

import pytest

from apps.api.app.live_database import build_live_engine, init_live_database
from apps.api.app.live_monitor import LiveCryptoMonitor
from apps.api.app.live_persistence import AsyncSqlLiveTickJournal
from services.market_data import MarketTick, PublicFeedHealth


class DisconnectedAdapter:
    symbols = ("BTC", "ETH", "SOL")

    def health(self) -> PublicFeedHealth:
        return PublicFeedHealth(
            connected=False,
            connection_generation=0,
            connection_attempts=0,
            reconnect_count=0,
            frames_received=0,
            ticks_emitted=0,
            parse_error_count=0,
            connected_since=None,
            last_message_at=None,
            last_tick_at=None,
            last_error=None,
        )

    async def stream(self) -> AsyncIterator[MarketTick]:
        if False:
            yield _tick()


def _tick() -> MarketTick:
    now = datetime.now(UTC)
    return MarketTick(
        timestamp=now,
        venue="coinbase-public",
        symbol="BTC",
        bid=60_000.0,
        ask=60_001.0,
        last=60_000.5,
        volume=100.0,
        bid_size=1.0,
        ask_size=1.0,
        sequence=77,
    )


def _sqlite_url(path: Path) -> str:
    return f"sqlite+aiosqlite:///{path}"


@pytest.mark.asyncio
async def test_restored_history_is_queryable_but_does_not_rehydrate_live_state(
    tmp_path: Path,
) -> None:
    source_path = tmp_path / "live-source.db"
    restored_path = tmp_path / "live-restored.db"
    source_engine = build_live_engine(_sqlite_url(source_path))
    await init_live_database(source_engine)
    source_journal = AsyncSqlLiveTickJournal(source_engine, prune_every_writes=100)
    tick = _tick()
    received_at = datetime.now(UTC)

    try:
        assert await source_journal.append(
            tick,
            received_at=received_at,
            connection_generation=4,
        )
    finally:
        await source_engine.dispose()

    shutil.copy2(source_path, restored_path)

    restored_engine = build_live_engine(_sqlite_url(restored_path))
    restored_journal = AsyncSqlLiveTickJournal(restored_engine, prune_every_writes=100)
    monitor = LiveCryptoMonitor(
        DisconnectedAdapter(),
        journal=restored_journal,
        persistence_required=True,
    )

    try:
        page = await monitor.persisted_history_page("BTC", limit=10)
        assert page is not None
        assert [row.tick.sequence for row in page.items] == [77]
        payload = page.items[0].as_dict()
        assert payload["source"] == "PUBLIC_READ_ONLY_PERSISTED"
        assert payload["financial_connectivity"] is False
        assert payload["real_money_execution"] is False

        assert monitor.snapshot("BTC") is None
        status = monitor.status()
        assert status["receiving_data"] is False
        assert status["complete"] is False
        assert status["financial_connectivity"] is False
        assert status["real_money_execution"] is False
    finally:
        await restored_engine.dispose()
