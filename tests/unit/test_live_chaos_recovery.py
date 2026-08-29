from collections.abc import AsyncIterator
from datetime import UTC, datetime

import pytest

from apps.api.app.live_monitor import LiveCryptoMonitor
from services.market_data import (
    LiveTickJournalError,
    MarketTick,
    PersistedLiveTick,
    PublicFeedHealth,
)


class StableAdapter:
    symbols = ("BTC", "ETH", "SOL")

    def health(self) -> PublicFeedHealth:
        now = datetime.now(UTC)
        return PublicFeedHealth(
            connected=True,
            connection_generation=7,
            connection_attempts=1,
            reconnect_count=0,
            frames_received=1_000,
            ticks_emitted=1_000,
            parse_error_count=0,
            connected_since=now,
            last_message_at=now,
            last_tick_at=now,
            last_error=None,
        )

    async def stream(self) -> AsyncIterator[MarketTick]:
        if False:
            yield _tick(1)


class DeterministicFlakyJournal:
    def __init__(self, *, fail_every: int) -> None:
        self.fail_every = fail_every
        self.rows: list[PersistedLiveTick] = []
        self.write_failures = 0

    async def append(
        self,
        tick: MarketTick,
        *,
        received_at: datetime,
        connection_generation: int,
    ) -> bool:
        if tick.sequence % self.fail_every == 0:
            self.write_failures += 1
            raise LiveTickJournalError("deterministic transient storage failure")
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
        return [row for row in reversed(self.rows) if row.tick.symbol == symbol][:limit]

    async def prune_before(self, cutoff: datetime) -> int:
        before = len(self.rows)
        self.rows = [row for row in self.rows if row.received_at >= cutoff]
        return before - len(self.rows)

    def status(self) -> dict[str, object]:
        return {
            "backend": "deterministic-flaky",
            "write_healthy": True,
            "read_healthy": True,
            "write_failures": self.write_failures,
        }


def _tick(sequence: int) -> MarketTick:
    price = 60_000.0 + sequence / 100.0
    return MarketTick(
        timestamp=datetime.now(UTC),
        venue="coinbase-public",
        symbol="BTC",
        bid=price,
        ask=price + 1.0,
        last=price + 0.5,
        volume=100.0 + sequence,
        bid_size=1.0,
        ask_size=1.0,
        sequence=sequence,
    )


@pytest.mark.asyncio
async def test_required_durability_recovers_across_repeated_transient_failures() -> None:
    journal = DeterministicFlakyJournal(fail_every=17)
    monitor = LiveCryptoMonitor(
        StableAdapter(),
        journal=journal,
        persistence_required=True,
    )

    accepted = 0
    rejected = 0
    for sequence in range(1, 1_001):
        if await monitor.ingest_tick(_tick(sequence)):
            accepted += 1
        else:
            rejected += 1

    expected_failures = 1_000 // 17
    assert rejected == expected_failures
    assert accepted == 1_000 - expected_failures
    assert len(journal.rows) == accepted

    latest = monitor.snapshot("BTC")
    assert latest is not None
    assert latest["sequence"] == 1_000
    assert latest["financial_connectivity"] is False
    assert latest["real_money_execution"] is False

    persistence = monitor.persistence_status()
    assert persistence["required"] is True
    assert persistence["healthy"] is True
    assert persistence["write_failures_current_connection"] == expected_failures
    assert persistence["persisted_current_connection"] == accepted

    analytics = monitor.analytics("BTC")
    assert analytics is not None
    assert analytics["sample_count"] == 512
    assert analytics["financial_connectivity"] is False
    assert analytics["real_money_execution"] is False
