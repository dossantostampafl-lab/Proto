from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
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


def _as_mapping(value: object) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise PublicCryptoFeedError("expected object payload")
    return value


def _decode_payload(message: str | bytes | Mapping[str, Any]) -> Mapping[str, Any]:
    try:
        if isinstance(message, bytes):
            payload = json.loads(message.decode("utf-8"))
        elif isinstance(message, str):
            payload = json.loads(message)
        else:
            payload = dict(message)
    except (UnicodeDecodeError, json.JSONDecodeError, TypeError, ValueError) as error:
        raise PublicCryptoFeedError("invalid public feed payload") from error
    return _as_mapping(payload)


def _parse_timestamp(value: object) -> datetime:
    if not isinstance(value, str):
        raise PublicCryptoFeedError("ticker timestamp is missing")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise PublicCryptoFeedError("ticker timestamp is invalid") from error
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise PublicCryptoFeedError("ticker timestamp must be timezone-aware")
    return parsed.astimezone(UTC)


def parse_public_ticker_message(
    message: str | bytes | Mapping[str, Any],
) -> list[MarketTick]:
    root = _decode_payload(message)
    if root.get("channel") != "ticker":
        return []

    timestamp = _parse_timestamp(root.get("timestamp"))
    try:
        sequence = int(root.get("sequence_num", 0))
    except (TypeError, ValueError) as error:
        raise PublicCryptoFeedError("ticker sequence is invalid") from error
    if sequence < 0:
        raise PublicCryptoFeedError("ticker sequence must be non-negative")

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
        self._connection_generation = 0
        self._connection_attempts = 0
        self._reconnect_count = 0
        self._frames_received = 0
        self._ticks_emitted = 0
        self._parse_error_count = 0
        self._connected_since: datetime | None = None
        self._last_message_at: datetime | None = None
        self._last_tick_at: datetime | None = None
        self._last_error: str | None = None

    @property
    def symbols(self) -> tuple[str, ...]:
        return tuple(_SUPPORTED_PRODUCTS[product] for product in self.products)

    def health(self) -> PublicFeedHealth:
        return PublicFeedHealth(
            connected=self._connected,
            connection_generation=self._connection_generation,
            connection_attempts=self._connection_attempts,
            reconnect_count=self._reconnect_count,
            frames_received=self._frames_received,
            ticks_emitted=self._ticks_emitted,
            parse_error_count=self._parse_error_count,
            connected_since=self._connected_since,
            last_message_at=self._last_message_at,
            last_tick_at=self._last_tick_at,
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
                    self._connection_generation += 1
                    self._connected_since = datetime.now(UTC)
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
                        observed_at = datetime.now(UTC)
                        self._frames_received += 1
                        self._last_message_at = observed_at
                        try:
                            ticks = parse_public_ticker_message(message)
                        except PublicCryptoFeedError:
                            self._parse_error_count += 1
                            raise
                        for tick in ticks:
                            self._ticks_emitted += 1
                            self._last_tick_at = observed_at
                            yield tick
                self._connected = False
                self._connected_since = None
                self._reconnect_count += 1
                await asyncio.sleep(delay)
            except asyncio.CancelledError:
                self._connected = False
                self._connected_since = None
                raise
            except Exception as error:
                self._connected = False
                self._connected_since = None
                self._last_error = type(error).__name__
                self._reconnect_count += 1
                await asyncio.sleep(delay)
                delay = min(delay * 2.0, self.reconnect_max_seconds)
