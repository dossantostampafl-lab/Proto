from __future__ import annotations

import asyncio
from collections.abc import Callable
from datetime import UTC, datetime
from enum import StrEnum

from pydantic import BaseModel, Field, field_validator

from services.market_data.core import (
    DataQualityMonitor,
    MarketTick,
    compute_orderbook_metrics,
)
from services.market_data.live import (
    SUPPORTED_LIVE_SYMBOLS,
    AsyncMarketDataAdapter,
    BinancePublicWebSocketAdapter,
)

from .models import SystemMode
from .research import metrics
from .settings import settings
from .websockets import WebSocketHub


class LiveFeedState(StrEnum):
    STOPPED = "STOPPED"
    CONNECTING = "CONNECTING"
    STREAMING = "STREAMING"
    BACKOFF = "BACKOFF"


class LiveDataStartRequest(BaseModel):
    source: str = Field(default="binance", max_length=32)
    symbol: str = Field(default="BTCUSDT", max_length=16)

    @field_validator("source")
    @classmethod
    def validate_source(cls, value: str) -> str:
        normalized = value.lower()
        if normalized != "binance":
            raise ValueError("only the allowlisted binance public source is supported")
        return normalized

    @field_validator("symbol")
    @classmethod
    def validate_symbol(cls, value: str) -> str:
        normalized = value.upper()
        if normalized not in SUPPORTED_LIVE_SYMBOLS:
            raise ValueError("unsupported live symbol")
        return normalized


AdapterFactory = Callable[[str, str], AsyncMarketDataAdapter]


def default_adapter_factory(source: str, symbol: str) -> AsyncMarketDataAdapter:
    if source != "binance":
        raise ValueError("unsupported live source")
    return BinancePublicWebSocketAdapter(
        symbol=symbol,
        connect_timeout_seconds=settings.live_data_connect_timeout_seconds,
    )


class LiveDataController:
    def __init__(
        self,
        *,
        websocket_hub: WebSocketHub,
        adapter_factory: AdapterFactory = default_adapter_factory,
        stale_after_seconds: float | None = None,
        max_backoff_seconds: float | None = None,
    ) -> None:
        self._hub = websocket_hub
        self._adapter_factory = adapter_factory
        self._stale_after_seconds = (
            stale_after_seconds
            if stale_after_seconds is not None
            else settings.live_data_stale_after_seconds
        )
        self._max_backoff_seconds = (
            max_backoff_seconds
            if max_backoff_seconds is not None
            else settings.live_data_max_backoff_seconds
        )
        self._monitor = DataQualityMonitor(stale_after_seconds=self._stale_after_seconds)
        self._task: asyncio.Task[None] | None = None
        self._lock = asyncio.Lock()
        self._desired_running = False
        self.state = LiveFeedState.STOPPED
        self.source = settings.live_data_source
        self.symbol = settings.live_data_symbol
        self.last_tick_at: datetime | None = None
        self.last_sequence: int | None = None
        self.received = 0
        self.rejected = 0
        self.reconnect_attempts = 0
        self.last_error: str | None = None
        self.latency_ms: float | None = None

    async def start(self, request: LiveDataStartRequest) -> dict[str, object]:
        async with self._lock:
            if self._task is not None and not self._task.done():
                if self.source == request.source and self.symbol == request.symbol:
                    return self.status()
                await self._stop_locked()
            self.source = request.source
            self.symbol = request.symbol
            self._monitor.reset()
            self.last_tick_at = None
            self.last_sequence = None
            self.received = 0
            self.rejected = 0
            self.reconnect_attempts = 0
            self.last_error = None
            self.latency_ms = None
            self._desired_running = True
            self.state = LiveFeedState.CONNECTING
            self._task = asyncio.create_task(self._run(), name="live-data-read-only")
        return self.status()

    async def stop(self) -> dict[str, object]:
        async with self._lock:
            await self._stop_locked()
        return self.status()

    async def _stop_locked(self) -> None:
        self._desired_running = False
        task = self._task
        self._task = None
        if task is not None and not task.done():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        self.state = LiveFeedState.STOPPED

    async def _run(self) -> None:
        backoff_seconds = 1.0
        try:
            while self._desired_running:
                self.state = LiveFeedState.CONNECTING
                try:
                    adapter = self._adapter_factory(self.source, self.symbol)
                    async for tick in adapter.stream():
                        if not self._desired_running:
                            return
                        self.state = LiveFeedState.STREAMING
                        self.last_error = None
                        backoff_seconds = 1.0
                        await self._publish(tick)
                    if self._desired_running:
                        raise ConnectionError("public market-data stream ended")
                except asyncio.CancelledError:
                    raise
                except Exception as error:
                    self.last_error = f"{type(error).__name__}: {error}"
                    self.reconnect_attempts += 1
                    metrics.increment("live_data_reconnects")
                    self.state = LiveFeedState.BACKOFF
                    await asyncio.sleep(backoff_seconds)
                    backoff_seconds = min(backoff_seconds * 2, self._max_backoff_seconds)
        finally:
            self.state = LiveFeedState.STOPPED

    async def _publish(self, tick: MarketTick) -> None:
        received_at = datetime.now(UTC)
        self.received += 1
        report = self._monitor.evaluate(tick, now=received_at)
        if not report.valid:
            self.rejected += 1
            metrics.increment("live_data_rejected")
            return

        self.last_tick_at = received_at
        self.last_sequence = tick.sequence
        self.latency_ms = max((received_at - tick.timestamp).total_seconds() * 1_000, 0.0)
        metrics.increment("live_data_ticks")
        book = compute_orderbook_metrics(tick)
        market_data = {
            **tick.model_dump(mode="json"),
            "source": self.source,
            "mid": tick.mid,
            "spread": tick.spread,
            "latency_ms": self.latency_ms,
            "read_only": True,
        }
        orderbook = {
            "timestamp": tick.timestamp.isoformat(),
            "source": self.source,
            "venue": tick.venue,
            "symbol": tick.symbol,
            **book.model_dump(mode="json"),
            "sequence": tick.sequence,
            "read_only": True,
        }
        await asyncio.gather(
            self._hub.broadcast("market-data", {"type": "market-data", "data": market_data}),
            self._hub.broadcast("orderbook", {"type": "orderbook", "data": orderbook}),
        )

    def status(self) -> dict[str, object]:
        now = datetime.now(UTC)
        staleness_ms = (
            max((now - self.last_tick_at).total_seconds() * 1_000, 0.0)
            if self.last_tick_at is not None
            else None
        )
        stale = self._desired_running and (
            staleness_ms is None or staleness_ms > self._stale_after_seconds * 1_000
        )
        return {
            "mode": SystemMode.LIVE_DATA_READ_ONLY,
            "state": self.state,
            "source": self.source,
            "symbol": self.symbol,
            "last_tick_at": self.last_tick_at,
            "last_sequence": self.last_sequence,
            "received": self.received,
            "rejected": self.rejected,
            "reconnect_attempts": self.reconnect_attempts,
            "last_error": self.last_error,
            "stale": stale,
            "latency_ms": self.latency_ms,
            "staleness_ms": staleness_ms,
            "read_only": True,
        }
