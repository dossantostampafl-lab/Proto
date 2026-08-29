from __future__ import annotations

import asyncio
from collections import defaultdict, deque
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress
from dataclasses import asdict
from datetime import UTC, datetime

from fastapi import APIRouter, HTTPException, Response

from services.analytics.live_market import calculate_live_market_analytics
from services.market_data import (
    CoinbasePublicMarketDataAdapter,
    DataQualityMonitor,
    MarketTick,
    compute_orderbook_metrics,
)

from .event_state import event_runtime
from .metrics_state import metrics
from .models import SystemMode
from .settings import settings
from .websockets import hub

_HISTORY_LIMIT = 512


class LiveCryptoMonitor:
    def __init__(self) -> None:
        self._adapter = CoinbasePublicMarketDataAdapter()
        self._quality = DataQualityMonitor(stale_after_seconds=10.0)
        self._task: asyncio.Task[None] | None = None
        self._latest: dict[str, MarketTick] = {}
        self._history: dict[str, deque[MarketTick]] = defaultdict(
            lambda: deque(maxlen=_HISTORY_LIMIT)
        )
        self._last_error: str | None = None

    @property
    def running(self) -> bool:
        return self._task is not None and not self._task.done()

    async def start(self) -> None:
        if self.running:
            return
        self._last_error = None
        self._task = asyncio.create_task(self._run(), name="live-crypto-monitor")
        metrics.increment("live_monitor_starts")
        await event_runtime.safe_publish(
            "proto.system",
            {
                "event": "live_monitor_started",
                "mode": SystemMode.LIVE_MONITORING.value,
                "financial_connectivity": "false",
                "real_money_execution": "false",
            },
        )

    async def stop(self) -> None:
        task = self._task
        if task is None:
            return
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task
        self._task = None
        metrics.increment("live_monitor_stops")
        await event_runtime.safe_publish(
            "proto.system",
            {
                "event": "live_monitor_stopped",
                "mode": SystemMode.LIVE_MONITORING.value,
            },
        )

    def status(self) -> dict[str, object]:
        latest = max(
            (tick.timestamp for tick in self._latest.values()),
            default=None,
        )
        age_seconds: float | None = None
        stale = True
        if latest is not None:
            age_seconds = max((datetime.now(UTC) - latest).total_seconds(), 0.0)
            stale = age_seconds > 10.0
        return {
            "mode": SystemMode.LIVE_MONITORING,
            "running": self.running,
            "receiving_data": bool(self._latest) and not stale,
            "stale": stale,
            "source": "PUBLIC_READ_ONLY",
            "latest_observed_at": latest.isoformat() if latest is not None else None,
            "last_frame_age_seconds": round(age_seconds, 6) if age_seconds is not None else None,
            "financial_connectivity": False,
            "real_money_execution": False,
            "symbols": sorted(self._latest),
            "history_limit_per_symbol": _HISTORY_LIMIT,
            "last_error": self._last_error,
        }

    def snapshots(self) -> list[dict[str, object]]:
        return [self._market_payload(self._latest[symbol]) for symbol in sorted(self._latest)]

    def snapshot(self, symbol: str) -> dict[str, object] | None:
        tick = self._latest.get(symbol.upper())
        return self._market_payload(tick) if tick is not None else None

    def analytics(self, symbol: str) -> dict[str, object] | None:
        history = self._history.get(symbol.upper())
        if not history:
            return None
        result = calculate_live_market_analytics(list(history))
        return {
            **asdict(result),
            "source": "PUBLIC_READ_ONLY_DESCRIPTIVE",
            "financial_connectivity": False,
            "real_money_execution": False,
        }

    async def ingest_tick(self, tick: MarketTick) -> bool:
        report = self._quality.evaluate(tick)
        if not report.valid:
            metrics.increment("live_market_frames_rejected")
            for issue in report.issues:
                metrics.increment(f"live_data_quality_{issue.value.lower()}")
            return False

        self._latest[tick.symbol] = tick
        self._history[tick.symbol].append(tick)
        metrics.increment("live_market_frames")
        book = compute_orderbook_metrics(tick)
        await hub.broadcast(
            "market-data",
            {"type": "market-data", "data": self._market_payload(tick)},
        )
        await hub.broadcast(
            "orderbook",
            {
                "type": "orderbook",
                "data": {
                    "timestamp": tick.timestamp.isoformat(),
                    "source": "PUBLIC_READ_ONLY",
                    "symbol": tick.symbol,
                    "best_bid": book.best_bid,
                    "best_ask": book.best_ask,
                    "bid_size": tick.bid_size,
                    "ask_size": tick.ask_size,
                    "mid_price": book.mid_price,
                    "spread": book.spread,
                    "microprice": book.microprice,
                    "imbalance": book.imbalance,
                    "depth": book.depth,
                    "financial_connectivity": False,
                    "real_money_execution": False,
                },
            },
        )
        return True

    async def _run(self) -> None:
        try:
            async for tick in self._adapter.stream():
                await self.ingest_tick(tick)
        except asyncio.CancelledError:
            raise
        except Exception as error:
            self._last_error = type(error).__name__
            metrics.increment("live_monitor_failures")
            await event_runtime.safe_publish(
                "proto.system",
                {
                    "event": "live_monitor_failure",
                    "error_type": self._last_error,
                },
            )

    @staticmethod
    def _market_payload(tick: MarketTick) -> dict[str, object]:
        return {
            "timestamp": tick.timestamp.isoformat(),
            "source": "PUBLIC_READ_ONLY",
            "venue": tick.venue,
            "symbol": tick.symbol,
            "bid": tick.bid,
            "ask": tick.ask,
            "mid": tick.mid,
            "last": tick.last,
            "spread": tick.spread,
            "volume_24h": tick.volume,
            "bid_size": tick.bid_size,
            "ask_size": tick.ask_size,
            "sequence": tick.sequence,
            "financial_connectivity": False,
            "real_money_execution": False,
        }


live_monitor = LiveCryptoMonitor()


@asynccontextmanager
async def live_router_lifespan(_: APIRouter) -> AsyncIterator[None]:
    should_autostart = (
        settings.system_mode == SystemMode.LIVE_MONITORING.value
        and settings.live_monitoring_autostart
    )
    if should_autostart:
        await live_monitor.start()
    try:
        yield
    finally:
        if live_monitor.running:
            await live_monitor.stop()


router = APIRouter(
    prefix="/live",
    tags=["live-monitoring"],
    lifespan=live_router_lifespan,
)


@router.get("/status")
def live_status() -> dict[str, object]:
    return live_monitor.status()


@router.get("/ready")
def live_ready(response: Response) -> dict[str, object]:
    status = live_monitor.status()
    ready = bool(status["running"] and status["receiving_data"] and not status["stale"])
    if not ready:
        response.status_code = 503
    return {
        "status": "ready" if ready else "not_ready",
        **status,
    }


@router.get("/market-data")
def live_market_data() -> dict[str, object]:
    snapshots = live_monitor.snapshots()
    return {
        "mode": SystemMode.LIVE_MONITORING,
        "source": "PUBLIC_READ_ONLY",
        "count": len(snapshots),
        "markets": snapshots,
        "financial_connectivity": False,
        "real_money_execution": False,
    }


@router.get("/market-data/{symbol}")
def live_market_data_symbol(symbol: str) -> dict[str, object]:
    snapshot = live_monitor.snapshot(symbol)
    if snapshot is None:
        raise HTTPException(status_code=404, detail="no live snapshot available for symbol")
    return snapshot


@router.get("/analytics/{symbol}")
def live_analytics_symbol(symbol: str) -> dict[str, object]:
    analytics = live_monitor.analytics(symbol)
    if analytics is None:
        raise HTTPException(status_code=404, detail="no live analytics available for symbol")
    return analytics


@router.post("/start")
async def live_start() -> dict[str, object]:
    await live_monitor.start()
    return live_monitor.status()


@router.post("/stop")
async def live_stop() -> dict[str, object]:
    await live_monitor.stop()
    return live_monitor.status()
