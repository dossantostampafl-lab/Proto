from __future__ import annotations

import json
from collections.abc import Sequence
from contextlib import AbstractAsyncContextManager
from typing import Any
from urllib.parse import urlsplit

from websockets.asyncio.client import connect

from .core import MarketTick
from .live_contracts import PublicFeedHealth as PublicFeedHealth
from .live_contracts import PublicMarketDataAdapter as PublicMarketDataAdapter
from .public_feed_parser import (
    SUPPORTED_PUBLIC_PRODUCTS,
    PublicCryptoFeedError,
    parse_public_ticker_message,
)
from .public_ws_adapter import PublicWebSocketTickerAdapter, receive_with_timeout

_COINBASE_PUBLIC_WS = "wss://advanced-trade-ws.coinbase.com"
_COINBASE_PUBLIC_HOST = "advanced-trade-ws.coinbase.com"


class PublicFeedTimeoutError(PublicCryptoFeedError):
    """Raised when an established public feed stops producing messages."""


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
    return await receive_with_timeout(
        websocket,
        timeout_seconds,
        timeout_error=PublicFeedTimeoutError,
        label="public feed",
    )


class CoinbasePublicMarketDataAdapter(PublicWebSocketTickerAdapter):
    """Read-only public crypto WebSocket feed with no account or trading credentials."""

    _timeout_error = PublicFeedTimeoutError

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
        unsupported = set(resolved_products).difference(SUPPORTED_PUBLIC_PRODUCTS)
        if unsupported:
            raise ValueError(f"unsupported public products: {sorted(unsupported)}")
        super().__init__(
            reconnect_min_seconds=reconnect_min_seconds,
            reconnect_max_seconds=reconnect_max_seconds,
            message_timeout_seconds=message_timeout_seconds,
            max_consecutive_parse_errors=max_consecutive_parse_errors,
        )
        self.products = resolved_products
        self.endpoint = _validate_public_endpoint(endpoint)

    @property
    def symbols(self) -> tuple[str, ...]:
        return tuple(SUPPORTED_PUBLIC_PRODUCTS[product] for product in self.products)

    def _connect(self, **options: Any) -> AbstractAsyncContextManager[Any]:
        return connect(self.endpoint, **options)

    async def _on_connected(self, websocket: Any) -> None:
        await websocket.send(
            json.dumps(
                {
                    "type": "subscribe",
                    "product_ids": list(self.products),
                    "channel": "ticker",
                }
            )
        )
        await websocket.send(json.dumps({"type": "subscribe", "channel": "heartbeats"}))

    async def _receive(self, websocket: Any) -> str | bytes:
        return await _receive_with_timeout(websocket, self.message_timeout_seconds)

    def _parse_frame(self, message: str | bytes) -> list[MarketTick]:
        return parse_public_ticker_message(message)
