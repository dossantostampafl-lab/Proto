from __future__ import annotations

from collections.abc import Sequence
from contextlib import AbstractAsyncContextManager
from typing import Any
from urllib.parse import urlsplit

from websockets.asyncio.client import connect

from .binance_public_feed import SUPPORTED_BINANCE_SYMBOLS, parse_binance_public_ticker_message
from .core import MarketTick
from .public_feed_parser import PublicCryptoFeedError
from .public_ws_adapter import PublicWebSocketTickerAdapter, receive_with_timeout

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


async def _receive_with_timeout(websocket: Any, timeout_seconds: float) -> str | bytes:
    return await receive_with_timeout(
        websocket,
        timeout_seconds,
        timeout_error=BinancePublicFeedTimeoutError,
        label="Binance public feed",
    )


class BinancePublicMarketDataAdapter(PublicWebSocketTickerAdapter):
    """Anonymous Binance Spot market-data feed; no keys, accounts, or order endpoints."""

    _timeout_error = BinancePublicFeedTimeoutError

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
        super().__init__(
            reconnect_min_seconds=reconnect_min_seconds,
            reconnect_max_seconds=reconnect_max_seconds,
            message_timeout_seconds=message_timeout_seconds,
            max_consecutive_parse_errors=max_consecutive_parse_errors,
        )

    @property
    def symbols(self) -> tuple[str, ...]:
        return tuple(SUPPORTED_BINANCE_SYMBOLS[product] for product in self.products)

    def _connect(self, **options: Any) -> AbstractAsyncContextManager[Any]:
        return connect(self.endpoint, **options)

    async def _receive(self, websocket: Any) -> str | bytes:
        return await _receive_with_timeout(websocket, self.message_timeout_seconds)

    def _parse_frame(self, message: str | bytes) -> list[MarketTick]:
        return parse_binance_public_ticker_message(message)
