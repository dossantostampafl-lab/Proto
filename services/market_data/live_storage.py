from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from .core import MarketTick


class LiveTickJournalError(RuntimeError):
    """Raised when durable read-only live-market storage is unavailable."""


class LiveHistoryCursorError(ValueError):
    """Raised when a persisted-history cursor is malformed or unsupported."""


@dataclass(frozen=True, slots=True)
class PersistedLiveTick:
    tick: MarketTick
    received_at: datetime
    connection_generation: int
    persisted_at: datetime

    def as_dict(self) -> dict[str, object]:
        return {
            "timestamp": self.tick.timestamp.isoformat(),
            "received_at": self.received_at.isoformat(),
            "persisted_at": self.persisted_at.isoformat(),
            "source": "PUBLIC_READ_ONLY_PERSISTED",
            "venue": self.tick.venue,
            "symbol": self.tick.symbol,
            "connection_generation": self.connection_generation,
            "bid": self.tick.bid,
            "ask": self.tick.ask,
            "mid": self.tick.mid,
            "last": self.tick.last,
            "spread": self.tick.spread,
            "volume_24h": self.tick.volume,
            "bid_size": self.tick.bid_size,
            "ask_size": self.tick.ask_size,
            "sequence": self.tick.sequence,
            "financial_connectivity": False,
            "real_money_execution": False,
        }


@dataclass(frozen=True, slots=True)
class PersistedLiveTickPage:
    items: Sequence[PersistedLiveTick]
    next_cursor: str | None


class LiveTickJournal(Protocol):
    """Durable append/query contract for accepted public market observations."""

    async def append(
        self,
        tick: MarketTick,
        *,
        received_at: datetime,
        connection_generation: int,
    ) -> bool: ...

    async def list_recent(
        self,
        *,
        symbol: str,
        limit: int = 100,
    ) -> Sequence[PersistedLiveTick]: ...

    async def list_page(
        self,
        *,
        symbol: str,
        limit: int = 100,
        cursor: str | None = None,
        start_at: datetime | None = None,
        end_at: datetime | None = None,
    ) -> PersistedLiveTickPage: ...

    async def prune_before(self, cutoff: datetime) -> int: ...

    def status(self) -> Mapping[str, object]: ...
