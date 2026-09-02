from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Sequence
from datetime import UTC, datetime
from math import isfinite
from urllib.parse import urlsplit

from websockets.asyncio.client import connect

from .binance_public_feed import SUPPORTED_BINANCE_SYMBOLS, parse_binance_public_ticker_message
from .core import MarketTick
from .live_contracts import PublicFeedHealth
from .public_feed_parser import MAX_PUBLIC_FRAME_BYTES, PublicCryptoFeedError

_BINANCE_PUBLIC_BASE = "wss://stream.binance.com:9443/stream"
_BINANCE_PUBLIC_HOST = "stream.binance.com"


class BinancePublicFeedTimeoutError(PublicCryptoFeedError):
    """Raised when an established Binance public feed stops producing messages."""


def _validate_products(products: Sequence[str]) -> tuple[str, ...]:
    resolved = tuple(dict.fromkeys(product.upper() for product in products))
    if not resolved:
        raise ValueError("at least one Binance product is required")
    unsupported = set(resolved).difference(SUPPORTED_BINANCE_SYMBOLS)
    if unsupported:
        raise ValueError(f"unsupported Binance public products: {sorted(unsupported)}")
    return resolved


def _build_endpoint(products: Sequence[str], endpoint_base: str) -> str:
    if endpoint_base != endpoint_base.strip() or not endpoint_base:
        raise ValueError("Binance public endpoint must be canonical")
    try:
        parsed = urlsplit(endpoint_base)
        port = parsed.port
    except ValueError as error:
        raise ValueError("Binance public endpoint is invalid") from error
    if parsed.scheme != "wss" or parsed.hostname != _BINANCE_PUBLIC_HOST:
        raise ValueError("Binance public endpoint host is not allowlisted")
    if parsed.username is not None or parsed.password is not None:
        raise ValueError("Binance public endpoint must not contain credentials")
    if port not in (443, 9443):
        raise ValueError("Binance public endpoint must use an allowlisted TLS port")
    if parsed.path.rstrip("/") != "/stream" or parsed.query or parsed.fragment:
        raise ValueError("Binance public endpoint base must not contain query data")
    streams = "/".join(f"{product.lower()}@ticker" for product in products)
    return f"{endpoint_base}?streams={streams}"


async def _receive_with_timeout(websocket: object, timeout_seconds: float) -> str | bytes:
    try:
        message = await asyncio.wait_for(websocket.recv(), timeout=timeout_seconds)  # type: ignore[attr-defined]
    except TimeoutError as error:
        raise BinancePublicFeedTimeoutError("Binance public feed message timeout") from error
    if not isinstance(message, (str, bytes)):
        raise PublicCryptoFeedError("Binance public feed message must be text or bytes")
    return message


class BinancePublicMarketDataAdapter:
    """Anonymous Binance Spot market-data feed; no keys, accounts, or order endpoints."""

    def __init__(
        self,
        *,
        products: Sequence[str] = ("BTCUSDT", "ETHUSDT", "SOLUSDT"),
        endpoint_base: str = _BINANCE_PUBLIC_BASE,
        reconnect_min_seconds: float = 0.5,
        reconnect_max_seconds: float = 15.0,
        message_timeout_seconds: float = 35.0,
        max_consecutive_parse_errors: int = 3,
    ) -> None:
        self.products = _validate_products(products)
        self.endpoint = _build_endpoint(self.products, endpoint_base)
        if not (
            isfinite(reconnect_min_seconds)
            and isfinite(reconnect_max_seconds)
            and reconnect_min_seconds > 0
            and reconnect_max_seconds >= reconnect_min_seconds
        ):
            raise ValueError("invalid reconnect interval")
        if not isfinite(message_timeout_seconds) or message_timeout_seconds <= 0:
            raise ValueError("message_timeout_seconds must be positive and finite")
        if (
            isinstance(max_consecutive_parse_errors, bool)
            or not isinstance(max_consecutive_parse_errors, int)
            or max_consecutive_parse_errors <= 0
        ):
            raise ValueError("max_consecutive_parse_errors must be a positive integer")
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
        return tuple(SUPPORTED_BINANCE_SYMBOLS[product] for product in self.products)

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
            ticks = parse_binance_public_ticker_message(message)
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
                        max_size=MAX_PUBLIC_FRAME_BYTES,
                    ) as websocket:
                        self._connected = True
                        self._connection_generation += 1
                        self._connected_since = datetime.now(UTC)
                        self._last_error = None
                        self._consecutive_parse_errors = 0
                        while True:
                            try:
                                message = await _receive_with_timeout(
                                    websocket,
                                    self.message_timeout_seconds,
                                )
                            except BinancePublicFeedTimeoutError:
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
