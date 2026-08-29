from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from websockets.asyncio.client import connect

from .core import MarketTick

_COINBASE_PUBLIC_WS = "wss://advanced-trade-ws.coinbase.com"
_SUPPORTED_PRODUCTS: dict[str, str] = {
    "BTC-USD": "BTC",
    "ETH-USD": "ETH",
    "SOL-USD": "SOL",
}


class PublicCryptoFeedError(RuntimeError):
    """Raised when a public crypto market-data frame cannot be normalized safely."""


@dataclass(frozen=True, slots=True)
class PublicFeedHealth:
    connected: bool
    connection_attempts: int
    reconnect_count: int
    last_error: str | None


def _as_mapping(value: object) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise PublicCryptoFeedError("expected object payload")
    return value


def _parse_timestamp(value: object) -> datetime:
    if not isinstance(value, str):
        raise PublicCryptoFeedError("ticker timestamp is missing")
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def parse_public_ticker_message(
    message: str | bytes | Mapping[str, Any],
) -> list[MarketTick]:
    if isinstance(message, bytes):
        payload = json.loads(message.decode("utf-8"))
    elif isinstance(message, str):
        payload = json.loads(message)
    else:
        payload = dict(message)

    root = _as_mapping(payload)
    if root.get("channel") != "ticker":
        return []

    timestamp = _parse_timestamp(root.get("timestamp"))
    sequence = int(root.get("sequence_num", 0))
    ticks: list[MarketTick] = []

    events = root.get("events", [])
    if not isinstance(events, Sequence) or isinstance(events, (str, bytes)):
        raise PublicCryptoFeedError("ticker events must be an array")

    for event_value in events:
        event = _as_mapping(event_value)
        ticker_values = event.get("tickers", [])
        if not isinstance(ticker_values, Sequence) or isinstance(
            ticker_values,
            (str, bytes),
        ):
            raise PublicCryptoFeedError("ticker list must be an array")
        for ticker_value in ticker_values:
            ticker = _as_mapping(ticker_value)
            product_id = str(ticker.get("product_id", ""))
            symbol = _SUPPORTED_PRODUCTS.get(product_id)
            if symbol is None:
                continue
            try:
                tick = MarketTick(
                    timestamp=timestamp,
                    venue="coinbase-public",
                    symbol=symbol,
                    bid=float(ticker["best_bid"]),
                    ask=float(ticker["best_ask"]),
                    last=float(ticker["price"]),
                    volume=float(ticker.get("volume_24_h", 0.0)),
                    bid_size=float(ticker.get("best_bid_quantity", 0.0)),
                    ask_size=float(ticker.get("best_ask_quantity", 0.0)),
                    sequence=sequence,
                )
            except (KeyError, TypeError, ValueError) as error:
                raise PublicCryptoFeedError("invalid public ticker frame") from error
            ticks.append(tick)
    return ticks


class CoinbasePublicMarketDataAdapter:
    """Read-only public crypto WebSocket feed with no account or trading credentials."""

    def __init__(
        self,
        *,
        products: Sequence[str] = ("BTC-USD", "ETH-USD", "SOL-USD"),
        endpoint: str = _COINBASE_PUBLIC_WS,
        reconnect_min_seconds: float = 0.5,
        reconnect_max_seconds: float = 15.0,
    ) -> None:
        resolved_products = tuple(dict.fromkeys(products))
        if not resolved_products:
            raise ValueError("at least one product is required")
        unsupported = set(resolved_products).difference(_SUPPORTED_PRODUCTS)
        if unsupported:
            raise ValueError(f"unsupported public products: {sorted(unsupported)}")
        if reconnect_min_seconds <= 0 or reconnect_max_seconds < reconnect_min_seconds:
            raise ValueError("invalid reconnect interval")
        self.products = resolved_products
        self.endpoint = endpoint
        self.reconnect_min_seconds = reconnect_min_seconds
        self.reconnect_max_seconds = reconnect_max_seconds
        self._connected = False
        self._connection_attempts = 0
        self._reconnect_count = 0
        self._last_error: str | None = None

    @property
    def symbols(self) -> tuple[str, ...]:
        return tuple(_SUPPORTED_PRODUCTS[product] for product in self.products)

    def health(self) -> PublicFeedHealth:
        return PublicFeedHealth(
            connected=self._connected,
            connection_attempts=self._connection_attempts,
            reconnect_count=self._reconnect_count,
            last_error=self._last_error,
        )

    async def stream(self) -> AsyncIterator[MarketTick]:
        delay = self.reconnect_min_seconds
        while True:
            self._connection_attempts += 1
            try:
                async with connect(
                    self.endpoint,
                    ping_interval=20,
                    ping_timeout=20,
                    close_timeout=5,
                    max_size=2**20,
                ) as websocket:
                    self._connected = True
                    self._last_error = None
                    await websocket.send(
                        json.dumps(
                            {
                                "type": "subscribe",
                                "product_ids": list(self.products),
                                "channel": "ticker",
                            }
                        )
                    )
                    await websocket.send(
                        json.dumps({"type": "subscribe", "channel": "heartbeats"})
                    )
                    delay = self.reconnect_min_seconds
                    async for message in websocket:
                        for tick in parse_public_ticker_message(message):
                            yield tick
                self._connected = False
                self._reconnect_count += 1
                await asyncio.sleep(delay)
            except asyncio.CancelledError:
                self._connected = False
                raise
            except Exception as error:
                self._connected = False
                self._last_error = type(error).__name__
                self._reconnect_count += 1
                await asyncio.sleep(delay)
                delay = min(delay * 2.0, self.reconnect_max_seconds)
