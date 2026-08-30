from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from .core import MarketTick


@dataclass(frozen=True, slots=True)
class PublicFeedHealth:
    connected: bool
    connection_generation: int
    connection_attempts: int
    reconnect_count: int
    frames_received: int
    ticks_emitted: int
    parse_error_count: int
    connected_since: datetime | None
    last_message_at: datetime | None
    last_tick_at: datetime | None
    last_error: str | None
    message_timeout_count: int = 0
    consecutive_parse_errors: int = 0


class PublicMarketDataAdapter(Protocol):
    """Minimal read-only adapter contract used by the live monitor."""

    @property
    def symbols(self) -> tuple[str, ...]: ...

    def health(self) -> PublicFeedHealth: ...

    def stream(self) -> AsyncIterator[MarketTick]: ...


__all__ = ["PublicFeedHealth", "PublicMarketDataAdapter"]
