from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from math import isfinite

from websockets.asyncio.client import connect

from services.replay import ReplaySession

from .contracts import OrderBookSnapshot
from .live import (
    _COINBASE_PUBLIC_WS,
    _receive_with_timeout,
    _validate_public_endpoint,
    PublicFeedTimeoutError,
)
from .public_feed_parser import MAX_PUBLIC_FRAME_BYTES, SUPPORTED_PUBLIC_PRODUCTS
from .public_l2 import PublicL2Book, PublicL2IntegrityError


@dataclass(frozen=True, slots=True)
class PublicL2StreamHealth:
    connected: bool
    connection_generation: int
    connection_attempts: int
    reconnect_count: int
    frames_received: int
    snapshots_emitted: int
    integrity_error_count: int
    message_timeout_count: int
    connected_since: datetime | None
    last_message_at: datetime | None
    last_snapshot_at: datetime | None
    last_error: str | None
    last_sequence: int | None


class CoinbasePublicL2StreamAdapter:
    """Read-only public Coinbase L2 stream for research corpus collection."""

    def __init__(
        self,
        *,
        products: Sequence[str] = ("BTC-USD", "ETH-USD", "SOL-USD"),
        endpoint: str = _COINBASE_PUBLIC_WS,
        reconnect_min_seconds: float = 0.5,
        reconnect_max_seconds: float = 15.0,
        message_timeout_seconds: float = 35.0,
        max_levels_per_side: int = 10_000,
        snapshot_depth: int = 1_000,
    ) -> None:
        resolved_products = tuple(dict.fromkeys(products))
        if not resolved_products:
            raise ValueError("at least one public L2 product is required")
        unsupported = set(resolved_products).difference(SUPPORTED_PUBLIC_PRODUCTS)
        if unsupported:
            raise ValueError(f"unsupported public L2 products: {sorted(unsupported)}")
        reconnect_values_valid = bool(
            isfinite(reconnect_min_seconds)
            and isfinite(reconnect_max_seconds)
            and reconnect_min_seconds > 0
            and reconnect_max_seconds >= reconnect_min_seconds
        )
        if not reconnect_values_valid:
            raise ValueError("invalid public L2 reconnect interval")
        if not isfinite(message_timeout_seconds) or message_timeout_seconds <= 0:
            raise ValueError("message_timeout_seconds must be positive and finite")

        self.products = resolved_products
        self.endpoint = _validate_public_endpoint(endpoint)
        self.reconnect_min_seconds = reconnect_min_seconds
        self.reconnect_max_seconds = reconnect_max_seconds
        self.message_timeout_seconds = message_timeout_seconds
        self._book = PublicL2Book(
            max_levels_per_side=max_levels_per_side,
            snapshot_depth=snapshot_depth,
        )
        self._connected = False
        self._connection_generation = 0
        self._connection_attempts = 0
        self._reconnect_count = 0
        self._frames_received = 0
        self._snapshots_emitted = 0
        self._integrity_error_count = 0
        self._message_timeout_count = 0
        self._connected_since: datetime | None = None
        self._last_message_at: datetime | None = None
        self._last_snapshot_at: datetime | None = None
        self._last_error: str | None = None

    @property
    def symbols(self) -> tuple[str, ...]:
        return tuple(SUPPORTED_PUBLIC_PRODUCTS[product] for product in self.products)

    def health(self) -> PublicL2StreamHealth:
        return PublicL2StreamHealth(
            connected=self._connected,
            connection_generation=self._connection_generation,
            connection_attempts=self._connection_attempts,
            reconnect_count=self._reconnect_count,
            frames_received=self._frames_received,
            snapshots_emitted=self._snapshots_emitted,
            integrity_error_count=self._integrity_error_count,
            message_timeout_count=self._message_timeout_count,
            connected_since=self._connected_since,
            last_message_at=self._last_message_at,
            last_snapshot_at=self._last_snapshot_at,
            last_error=self._last_error,
            last_sequence=self._book.last_sequence,
        )

    def connection_replay_session(self, session_id: str, *, seed: int = 0) -> ReplaySession:
        return self._book.replay_session(session_id, seed=seed)

    def connection_corpus_fingerprint(self) -> str:
        return self._book.corpus_fingerprint()

    async def stream(self) -> AsyncIterator[OrderBookSnapshot]:
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
                        self._book.reset()
                        self._connected = True
                        self._connection_generation += 1
                        self._connected_since = datetime.now(UTC)
                        self._last_error = None
                        await websocket.send(
                            json.dumps(
                                {
                                    "type": "subscribe",
                                    "product_ids": list(self.products),
                                    "channel": "level2",
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
                            try:
                                snapshots = self._book.ingest(message)
                            except PublicL2IntegrityError:
                                self._integrity_error_count += 1
                                raise
                            if snapshots:
                                delay = self.reconnect_min_seconds
                            for snapshot in snapshots:
                                self._snapshots_emitted += 1
                                self._last_snapshot_at = observed_at
                                yield snapshot
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
