from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from math import isfinite
from typing import Any, Protocol
from urllib.parse import urlsplit

from websockets.asyncio.client import connect

from .core import MarketTick

_COINBASE_PUBLIC_WS = "wss://advanced-trade-ws.coinbase.com"
_COINBASE_PUBLIC_HOST = "advanced-trade-ws.coinbase.com"
_MAX_PUBLIC_FRAME_BYTES = 256 * 1024
_MAX_EVENTS_PER_FRAME = 32
_MAX_TICKERS_PER_EVENT = 32
_SUPPORTED_PRODUCTS: dict[str, str] = {
    "BTC-USD": "BTC",
    "ETH-USD": "ETH",
    "SOL-USD": "SOL",
}


class PublicCryptoFeedError(RuntimeError):
    """Raised when a public crypto market-data frame cannot be normalized safely."""


class PublicFeedTimeoutError(PublicCryptoFeedError):
    """Raised when an established public feed stops producing messages."""


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


def _as_mapping(value: object) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise PublicCryptoFeedError("expected object payload")
    return value


def _decode_payload(message: str | bytes | Mapping[str, Any]) -> Mapping[str, Any]:
    try:
        if isinstance(message, bytes):
            if len(message) > _MAX_PUBLIC_FRAME_BYTES:
                raise PublicCryptoFeedError("public feed payload exceeds size limit")
            payload = json.loads(message.decode("utf-8"))
        elif isinstance(message, str):
            if len(message.encode("utf-8")) > _MAX_PUBLIC_FRAME_BYTES:
                raise PublicCryptoFeedError("public feed payload exceeds size limit")
            payload = json.loads(message)
        else:
            payload = dict(message)
    except (UnicodeDecodeError, UnicodeEncodeError, json.JSONDecodeError, TypeError, ValueError) as error:
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


def _validate_public_endpoint(endpoint: str) -> str:
    if endpoint != endpoint.strip() or not endpoint:
        raise ValueError("public endpoint must be a canonical WSS URL")
    try:
        parsed = urlsplit(endpoint)
        port = parsed.port
    except ValueError as error:
        raise ValueError("public endpoint is invalid") from error
    if parsed.scheme != "wss":
        raise ValueError("public endpoint must use wss")
    if parsed.hostname != _COINBASE_PUBLIC_HOST:
        raise ValueError("public endpoint host is not allowlisted")
    if parsed.username is not None or parsed.password is not None:
        raise ValueError("public endpoint must not contain credentials")
    if port not in (None, 443):
        raise ValueError("public endpoint must use the standard TLS port")
    if parsed.path not in ("", "/") or parsed.query or parsed.fragment:
        raise ValueError("public endpoint must not contain path parameters or query data")
    return endpoint


async def _receive_with_timeout(websocket: Any, timeout_seconds: float) -> str | bytes:
    try:
        message = await asyncio.wait_for(websocket.recv(), timeout=timeout_seconds)
    except TimeoutError as error:
        raise PublicFeedTimeoutError("public feed message timeout") from error
    if not isinstance(message, (str, bytes)):
        raise PublicCryptoFeedError("public feed message must be text or bytes")
    return message


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
    if len(events) > _MAX_EVENTS_PER_FRAME:
        raise PublicCryptoFeedError("ticker event count exceeds limit")

    for event_value in events:
        event = _as_mapping(event_value)
        ticker_values = event.get("tickers", [])
        if not isinstance(ticker_values, Sequence) or isinstance(
            ticker_values,
            (str, bytes),
        ):
            raise PublicCryptoFeedError("ticker list must be an array")
        if len(ticker_values) > _MAX_TICKERS_PER_EVENT:
            raise PublicCryptoFeedError("ticker count exceeds limit")
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
        message_timeout_seconds: float = 35.0,
        max_consecutive_parse_errors: int = 3,
    ) -> None:
        resolved_products = tuple(dict.fromkeys(products))
        if not resolved_products:
            raise ValueError("at least one product is required")
        unsupported = set(resolved_products).difference(_SUPPORTED_PRODUCTS)
        if unsupported:
            raise ValueError(f"unsupported public products: {sorted(unsupported)}")
        reconnect_values_valid = bool(
            isfinite(reconnect_min_seconds)
            and isfinite(reconnect_max_seconds)
            and reconnect_min_seconds > 0
            and reconnect_max_seconds >= reconnect_min_seconds
        )
        if not reconnect_values_valid:
            raise ValueError("invalid reconnect interval")
        if not isfinite(message_timeout_seconds) or message_timeout_seconds <= 0:
            raise ValueError("message_timeout_seconds must be positive and finite")
        if (
            isinstance(max_consecutive_parse_errors, bool)
            or not isinstance(max_consecutive_parse_errors, int)
            or max_consecutive_parse_errors <= 0
        ):
            raise ValueError("max_consecutive_parse_errors must be a positive integer")
        self.products = resolved_products
        self.endpoint = _validate_public_endpoint(endpoint)
        self.reconnect_min_seconds = reconnect_min_seconds
        self.reconnect_max_seconds = reconnect_max_seconds
        self.message_timeout_seconds = message_timeout_seconds
        self.max_consecutive_parse_errors = max_consecutive_parse_errors
        self._connected = False
        self._connection_generation = 0
        self._connection_attempts = 0
        self._reconnect_count = 0
        self._frames_received = 0
        self._ticks_emitted = 0
        self._parse_error_count = 0
        self._consecutive_parse_errors = 0
        self._message_timeout_count = 0
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
            message_timeout_count=self._message_timeout_count,
            consecutive_parse_errors=self._consecutive_parse_errors,
        )

    def _parse_message(self, message: str | bytes) -> list[MarketTick]:
        try:
            ticks = parse_public_ticker_message(message)
        except PublicCryptoFeedError as error:
            self._parse_error_count += 1
            self._consecutive_parse_errors += 1
            self._last_error = type(error).__name__
            if self._consecutive_parse_errors >= self.max_consecutive_parse_errors:
                raise
            return []
        if ticks:
            self._consecutive_parse_errors = 0
            self._last_error = None
        return ticks

    async def stream(self) -> AsyncIterator[MarketTick]:
        delay = self.reconnect_min_seconds
        try:
            while True:
                self._connection_attempts += 1
                try:
                    async with connect(
                        self.endpoint,
                        ping_interval=20,
                        ping_timeout=20,
                        close_timeout=5,
                        max_size=_MAX_PUBLIC_FRAME_BYTES,
                    ) as websocket:
                        self._connected = True
                        self._connection_generation += 1
                        self._connected_since = datetime.now(UTC)
                        self._last_error = None
                        self._consecutive_parse_errors = 0
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
                        while True:
                            try:
                                message = await _receive_with_timeout(
                                    websocket,
                                    self.message_timeout_seconds,
                                )
                            except PublicFeedTimeoutError:
                                self._message_timeout_count += 1
                                raise
                            observed_at = datetime.now(UTC)
                            self._frames_received += 1
                            self._last_message_at = observed_at
                            ticks = self._parse_message(message)
                            if ticks:
                                delay = self.reconnect_min_seconds
                            for tick in ticks:
                                self._ticks_emitted += 1
                                self._last_tick_at = observed_at
                                yield tick
                except asyncio.CancelledError:
                    raise
                except Exception as error:
                    self._connected = False
                    self._connected_since = None
                    self._last_error = type(error).__name__
                    self._reconnect_count += 1
                    await asyncio.sleep(delay)
                    delay = min(delay * 2.0, self.reconnect_max_seconds)
        finally:
            self._connected = False
            self._connected_since = None
