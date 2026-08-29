from __future__ import annotations

import json
from collections.abc import AsyncIterator, Callable
from datetime import UTC, datetime
from typing import Any, Protocol

from websockets.asyncio.client import connect

from .core import MarketTick

BINANCE_PUBLIC_STREAM_HOST = "stream.binance.com:9443"
SUPPORTED_LIVE_SYMBOLS = frozenset({"BTCUSDT", "ETHUSDT", "SOLUSDT"})


class AsyncMarketDataAdapter(Protocol):
    def stream(self) -> AsyncIterator[MarketTick]: ...


def normalize_binance_book_ticker(
    payload: dict[str, Any], *, received_at: datetime | None = None
) -> MarketTick:
    """Normalize a public Binance book-ticker message without private account data."""
    symbol = str(payload.get("s", "")).upper()
    if symbol not in SUPPORTED_LIVE_SYMBOLS:
        raise ValueError("unsupported live symbol")

    received = received_at or datetime.now(UTC)
    event_time_ms = payload.get("E")
    timestamp = (
        datetime.fromtimestamp(float(event_time_ms) / 1_000, tz=UTC)
        if event_time_ms is not None
        else received
    )
    bid = float(payload["b"])
    ask = float(payload["a"])
    return MarketTick(
        timestamp=timestamp,
        venue="binance-public",
        symbol=symbol,
        bid=bid,
        ask=ask,
        last=(bid + ask) / 2,
        volume=0.0,
        bid_size=float(payload["B"]),
        ask_size=float(payload["A"]),
        sequence=int(payload["u"]),
    )


class BinancePublicWebSocketAdapter:
    """Read-only adapter for Binance's unauthenticated public market-data stream."""

    def __init__(
        self,
        *,
        symbol: str,
        connect_timeout_seconds: float = 10.0,
        connector: Callable[..., Any] = connect,
    ) -> None:
        normalized_symbol = symbol.upper()
        if normalized_symbol not in SUPPORTED_LIVE_SYMBOLS:
            raise ValueError("unsupported live symbol")
        self.symbol = normalized_symbol
        self.connect_timeout_seconds = connect_timeout_seconds
        self._connector = connector

    @property
    def uri(self) -> str:
        stream = f"{self.symbol.lower()}@bookTicker"
        return f"wss://{BINANCE_PUBLIC_STREAM_HOST}/ws/{stream}"

    async def stream(self) -> AsyncIterator[MarketTick]:
        async with self._connector(
            self.uri,
            open_timeout=self.connect_timeout_seconds,
            ping_interval=20,
            ping_timeout=20,
            close_timeout=5,
            max_size=16_384,
            max_queue=32,
        ) as websocket:
            async for raw_message in websocket:
                if not isinstance(raw_message, str):
                    raise ValueError("binary market-data messages are not accepted")
                payload = json.loads(raw_message)
                if not isinstance(payload, dict):
                    raise ValueError("market-data message must be an object")
                yield normalize_binance_book_ticker(payload)
