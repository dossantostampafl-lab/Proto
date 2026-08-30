from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime

import pytest

from apps.api.app.live_monitor import LiveCryptoMonitor
from services.market_data import (
    LiveTickJournalError,
    MarketTick,
    PersistedLiveTick,
    PersistedLiveTickPage,
    PublicFeedHealth,
)


class MutableAdapter:
    symbols = ("BTC", "ETH", "SOL")

    def __init__(self) -> None:
        self.generation = 1

    def health(self) -> PublicFeedHealth:
        now = datetime.now(UTC)
        return PublicFeedHealth(
            connected=True,
            connection_generation=self.generation,
            connection_attempts=self.generation,
            reconnect_count=max(self.generation - 1, 0),
            frames_received=100,
            ticks_emitted=100,
            parse_error_count=0,
            connected_since=now,
            last_message_at=now,
            last_tick_at=now,
            last_error=None,
        )

    async def stream(self) -> AsyncIterator[MarketTick]:
        if False:
            yield _tick(1)


class FaultInjectingJournal:
    def __init__(self) -> None:
        self.fail_next_write = False
        self.fail_next_read = False
        self.rows: list[PersistedLiveTick] = []
        self.write_failures = 0
        self.read_failures = 0

    async def append(
        self,
        tick: MarketTick,
        *,
        received_at: datetime,
        connection_generation: int,
    ) -> bool:
        if self.fail_next_write:
            self.fail_next_write = False
            self.write_failures += 1
            raise LiveTickJournalError("injected write outage")
        self.rows.append(
            PersistedLiveTick(
                tick=tick,
                received_at=received_at,
                connection_generation=connection_generation,
                persisted_at=received_at,
            )
        )
        return True

    async def list_recent(
        self,
        *,
        symbol: str,
        limit: int = 100,
    ) -> list[PersistedLiveTick]:
        page = await self.list_page(symbol=symbol, limit=limit)
        return list(page.items)

    async def list_page(
        self,
        *,
        symbol: str,
        limit: int = 100,
        cursor: str | None = None,
        start_at: datetime | None = None,
        end_at: datetime | None = None,
    ) -> PersistedLiveTickPage:
        del cursor, start_at, end_at
        if self.fail_next_read:
            self.fail_next_read = False
            self.read_failures += 1
            raise LiveTickJournalError("injected read outage")
        rows = [row for row in reversed(self.rows) if row.tick.symbol == symbol][:limit]
        return PersistedLiveTickPage(items=tuple(rows), next_cursor=None)

    async def prune_before(self, cutoff: datetime) -> int:
        before = len(self.rows)
        self.rows = [row for row in self.rows if row.received_at >= cutoff]
        return before - len(self.rows)

    def status(self) -> dict[str, object]:
        return {
            "backend": "fault-injecting",
            "write_healthy": True,
            "read_healthy": True,
            "write_failures": self.write_failures,
            "read_failures": self.read_failures,
        }


def _tick(sequence: int, *, symbol: str = "BTC") -> MarketTick:
    price = 60_000.0 + sequence
    return MarketTick(
        timestamp=datetime.now(UTC),
        venue="coinbase-public",
        symbol=symbol,
        bid=price,
        ask=price + 1.0,
        last=price + 0.5,
        volume=100.0 + sequence,
        bid_size=1.0,
        ask_size=1.0,
        sequence=sequence,
    )


@pytest.mark.asyncio
async def test_required_persistence_fails_closed_then_recovers() -> None:
    adapter = MutableAdapter()
    journal = FaultInjectingJournal()
    monitor = LiveCryptoMonitor(adapter, journal=journal, persistence_required=True)

    journal.fail_next_write = True
    assert await monitor.ingest_tick(_tick(1)) is False
    assert monitor.snapshot("BTC") is None
    degraded = monitor.persistence_status()
    assert degraded["healthy"] is False
    assert degraded["write_failures_current_connection"] == 1

    assert await monitor.ingest_tick(_tick(2)) is True
    recovered = monitor.snapshot("BTC")
    assert recovered is not None
    assert recovered["sequence"] == 2
    assert recovered["financial_connectivity"] is False
    assert recovered["real_money_execution"] is False
    assert monitor.persistence_status()["healthy"] is True


@pytest.mark.asyncio
async def test_generation_change_drops_old_snapshot_even_if_first_new_write_fails() -> None:
    adapter = MutableAdapter()
    journal = FaultInjectingJournal()
    monitor = LiveCryptoMonitor(adapter, journal=journal, persistence_required=True)

    assert await monitor.ingest_tick(_tick(10)) is True
    assert monitor.snapshot("BTC") is not None

    adapter.generation = 2
    journal.fail_next_write = True
    assert await monitor.ingest_tick(_tick(11)) is False
    assert monitor.snapshot("BTC") is None

    assert await monitor.ingest_tick(_tick(12)) is True
    recovered = monitor.snapshot("BTC")
    assert recovered is not None
    assert recovered["connection_generation"] == 2
    assert recovered["sequence"] == 12


@pytest.mark.asyncio
async def test_history_read_health_recovers_after_transient_backend_failure() -> None:
    adapter = MutableAdapter()
    journal = FaultInjectingJournal()
    monitor = LiveCryptoMonitor(adapter, journal=journal, persistence_required=True)
    assert await monitor.ingest_tick(_tick(21)) is True

    journal.fail_next_read = True
    with pytest.raises(LiveTickJournalError):
        await monitor.persisted_history_page("BTC", limit=10)

    degraded = monitor.persistence_status()
    assert degraded["write_healthy"] is True
    assert degraded["read_healthy"] is False
    assert degraded["read_failures"] == 1

    page = await monitor.persisted_history_page("BTC", limit=10)
    assert page is not None
    assert [row.tick.sequence for row in page.items] == [21]
    recovered = monitor.persistence_status()
    assert recovered["write_healthy"] is True
    assert recovered["read_healthy"] is True
    assert recovered["financial_connectivity"] is False
    assert recovered["real_money_execution"] is False
