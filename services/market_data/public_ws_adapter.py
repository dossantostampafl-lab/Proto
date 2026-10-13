from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import AbstractAsyncContextManager
from datetime import UTC, datetime
from math import isfinite
from typing import Any

from .core import MarketTick
from .live_contracts import PublicFeedHealth
from .public_feed_parser import MAX_PUBLIC_FRAME_BYTES, PublicCryptoFeedError


async def receive_with_timeout(
    websocket: Any,
    timeout_seconds: float,
    *,
    timeout_error: type[PublicCryptoFeedError],
    label: str,
) -> str | bytes:
    try:
        message = await asyncio.wait_for(websocket.recv(), timeout=timeout_seconds)
    except TimeoutError as error:
        raise timeout_error(f"{label} message timeout") from error
    if not isinstance(message, (str, bytes)):
        raise PublicCryptoFeedError(f"{label} message must be text or bytes")
    return message


class PublicWebSocketTickerAdapter:
    """Shared health, parse-error and reconnect handling for read-only public ticker feeds.

    Venue adapters supply the endpoint, the subscription handshake and frame parsing.
    """

    _timeout_error: type[PublicCryptoFeedError]

    def __init__(
        self,
        *,
        reconnect_min_seconds: float,
        reconnect_max_seconds: float,
        message_timeout_seconds: float,
        max_consecutive_parse_errors: int,
    ) -> None:
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

    def _connect(self, **options: Any) -> AbstractAsyncContextManager[Any]:
        raise NotImplementedError

    async def _on_connected(self, websocket: Any) -> None:
        """Send venue subscription messages, if the venue needs any."""

    async def _receive(self, websocket: Any) -> str | bytes:
        raise NotImplementedError

    def _parse_frame(self, message: str | bytes) -> list[MarketTick]:
        raise NotImplementedError

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
            ticks = self._parse_frame(message)
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
                    async with self._connect(
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
                        await self._on_connected(websocket)
                        while True:
                            try:
                                message = await self._receive(websocket)
                            except self._timeout_error:
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
