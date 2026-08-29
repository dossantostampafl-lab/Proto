from __future__ import annotations

import asyncio
from contextlib import suppress

from fastapi import APIRouter, HTTPException

from services.market_data import (
    CoinbasePublicMarketDataAdapter,
    DataQualityMonitor,
    MarketTick,
    compute_orderbook_metrics,
)

from .models import SystemMode
from .research import metrics
from .websockets import hub

router = APIRouter(prefix="/live", tags=["live-monitoring"])


class LiveCryptoMonitor:
    def __init__(self) -> None:
        self._adapter = CoinbasePublicMarketDataAdapter()
        self._quality = DataQualityMonitor(stale_after_seconds=10.0)
        self._task: asyncio.Task[None] | None = None
        self._latest: dict[str, MarketTick] = {}
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

    async def stop(self) -> None:
        task = self._task
        if task is None:
            return
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task
        self._task = None
        metrics.increment("live_monitor_stops")

    def status(self) -> dict[str, object]:
        return {
            "mode": SystemMode.LIVE_MONITORING,
            "running": self.running,
            "financial_connectivity": False,
            "real_money_execution": False,
            "symbols": sorted(self._latest),
            "last_error": self._last_error,
        }

    def snapshots(self) -> list[dict[str, object]]:
        return [
            self._market_payload(self._latest[symbol])
            for symbol in sorted(self._latest)
        ]

    def snapshot(self, symbol: str) -> dict[str, object] | None:
        tick = self._latest.get(symbol.upper())
        return self._market_payload(tick) if tick is not None else None

    async def _run(self) -> None:
        try:
            async for tick in self._adapter.stream():
                report = self._quality.evaluate(tick)
                if not report.valid:
                    metrics.increment("live_market_frames_rejected")
                    continue
                self._latest[tick.symbol] = tick
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
                        },
                    },
                )
        except asyncio.CancelledError:
            raise
        except Exception as error:
            self._last_error = type(error).__name__
            metrics.increment("live_monitor_failures")

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
        }


live_monitor = LiveCryptoMonitor()


@router.get("/status")
def live_status() -> dict[str, object]:
    return live_monitor.status()


@router.get("/market-data")
def live_market_data() -> dict[str, object]:
    snapshots = live_monitor.snapshots()
    return {"count": len(snapshots), "markets": snapshots}


@router.get("/market-data/{symbol}")
def live_market_data_symbol(symbol: str) -> dict[str, object]:
    snapshot = live_monitor.snapshot(symbol)
    if snapshot is None:
        raise HTTPException(status_code=404, detail="no live snapshot available for symbol")
    return snapshot


@router.post("/start")
async def live_start() -> dict[str, object]:
    await live_monitor.start()
    return live_monitor.status()


@router.post("/stop")
async def live_stop() -> dict[str, object]:
    await live_monitor.stop()
    return live_monitor.status()
