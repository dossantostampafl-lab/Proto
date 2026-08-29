from __future__ import annotations

import asyncio
from contextlib import suppress
from dataclasses import dataclass
from datetime import timedelta
from typing import Iterator

from fastapi import APIRouter, Query

from services.market_data.adapters import SyntheticAdapter
from services.market_data.core import MarketTick
from services.quant.core import compute_edge, estimate_probability

from .websockets import hub

router = APIRouter(prefix="/demo", tags=["demo"])


@dataclass(frozen=True)
class DemoAssetConfig:
    symbol: str
    start_price: float
    seed: int


DEMO_ASSETS = (
    DemoAssetConfig("BTC", 100_000.0, 101),
    DemoAssetConfig("ETH", 4_000.0, 202),
    DemoAssetConfig("SOL", 200.0, 303),
)
DEMO_EXPIRIES_MINUTES = (5, 15, 60, 240)


class DemoEngine:
    def __init__(self) -> None:
        self._streams: dict[str, Iterator[MarketTick]] = {}
        self._starts = {asset.symbol: asset.start_price for asset in DEMO_ASSETS}
        self._sequence = 0
        self.reset()

    def reset(self) -> None:
        self._streams = {
            asset.symbol: iter(
                SyntheticAdapter(
                    symbol=asset.symbol,
                    start_price=asset.start_price,
                    seed=asset.seed,
                    count=100_000,
                    interval_ms=1_000,
                ).stream()
            )
            for asset in DEMO_ASSETS
        }
        self._sequence = 0

    @property
    def sequence(self) -> int:
        return self._sequence

    def status(self) -> dict[str, object]:
        return {
            "enabled": True,
            "source": "SYNTHETIC_DETERMINISTIC",
            "sequence": self._sequence,
            "assets": [asset.symbol for asset in DEMO_ASSETS],
            "real_market_data": False,
            "real_money_execution": False,
        }

    def _market_probability(self, tick: MarketTick) -> float:
        start = self._starts[tick.symbol]
        relative_move = (tick.last / start) - 1.0
        return min(max(0.5 + relative_move * 8.0, 0.02), 0.98)

    def _model_feed(self, tick: MarketTick) -> dict[str, object]:
        mid = (tick.bid + tick.ask) / 2.0
        spread = tick.ask - tick.bid
        depth = tick.bid_size + tick.ask_size
        imbalance = (
            (tick.bid_size - tick.ask_size) / depth if depth > 0.0 else 0.0
        )
        market_probability = self._market_probability(tick)
        spread_bps = spread / mid * 10_000.0 if mid > 0.0 else 0.0
        volatility = min(0.80, 0.16 + spread_bps / 1_000.0)
        probability = estimate_probability(
            market_probability=market_probability,
            volatility=volatility,
            imbalance=imbalance,
        )
        edge = compute_edge(
            model_probability=probability.probability,
            market_probability=market_probability,
            fees=0.001,
            slippage=0.001,
            spread_cost=spread / max(tick.bid + tick.ask, 1e-9),
            hedge_cost=0.001,
            uncertainty_penalty=probability.uncertainty * 0.02,
            latency_penalty=0.0005,
            minimum_edge=0.01,
        )
        microprice = (
            (tick.ask * tick.bid_size + tick.bid * tick.ask_size) / depth
            if depth > 0.0
            else mid
        )
        return {
            "timestamp": tick.timestamp,
            "sequence": tick.sequence,
            "source": "SYNTHETIC_DETERMINISTIC",
            "symbol": tick.symbol,
            "spot": tick.last,
            "bid": tick.bid,
            "ask": tick.ask,
            "mid": mid,
            "spread": spread,
            "spread_bps": spread_bps,
            "bid_size": tick.bid_size,
            "ask_size": tick.ask_size,
            "volume": tick.volume,
            "imbalance": imbalance,
            "microprice": microprice,
            "market_probability": market_probability,
            "model_probability": probability.probability,
            "confidence": probability.confidence,
            "uncertainty": probability.uncertainty,
            "raw_edge": edge.raw_edge,
            "net_edge": edge.net_edge,
            "edge_decision": edge.decision,
            "model_version": probability.model_version,
            "feature_version": probability.feature_version,
        }

    def _resolution_grid(self, feeds: list[dict[str, object]]) -> list[dict[str, object]]:
        grid: list[dict[str, object]] = []
        for feed in feeds:
            timestamp = feed["timestamp"]
            for minutes in DEMO_EXPIRIES_MINUTES:
                expiry = timestamp + timedelta(minutes=minutes)
                net_edge = float(feed["net_edge"])
                grid.append(
                    {
                        "market_id": f"{str(feed['symbol']).lower()}-demo-{minutes}m",
                        "asset": feed["symbol"],
                        "expiry": expiry,
                        "time_to_expiry_seconds": minutes * 60,
                        "market_probability": feed["market_probability"],
                        "model_probability": feed["model_probability"],
                        "net_edge": net_edge,
                        "confidence": feed["confidence"],
                        "state": "SIGNAL" if net_edge > 0.01 else "ANALYZED",
                        "source": "SYNTHETIC_DETERMINISTIC",
                    }
                )
        return grid

    def next_frame(self) -> dict[str, object]:
        feeds = [self._model_feed(next(self._streams[asset.symbol])) for asset in DEMO_ASSETS]
        self._sequence += 1
        return {
            **self.status(),
            "sequence": self._sequence,
            "model_feed": feeds,
            "resolution_grid": self._resolution_grid(feeds),
        }


async def publish_demo_frame(frame: dict[str, object]) -> None:
    for feed in frame["model_feed"]:
        await hub.broadcast("market-data", {"type": "model-feed", "data": feed})
        await hub.broadcast(
            "orderbook",
            {
                "type": "orderbook",
                "data": {
                    "timestamp": feed["timestamp"],
                    "symbol": feed["symbol"],
                    "best_bid": feed["bid"],
                    "best_ask": feed["ask"],
                    "bid_size": feed["bid_size"],
                    "ask_size": feed["ask_size"],
                    "mid_price": feed["mid"],
                    "spread": feed["spread"],
                    "imbalance": feed["imbalance"],
                },
            },
        )
    await hub.broadcast(
        "analytics",
        {"type": "resolution-grid", "data": frame["resolution_grid"]},
    )


class DemoController:
    def __init__(self, engine: DemoEngine) -> None:
        self.engine = engine
        self._task: asyncio.Task[None] | None = None
        self.interval_ms = 1_000

    @property
    def running(self) -> bool:
        return self._task is not None and not self._task.done()

    def status(self) -> dict[str, object]:
        return {
            **self.engine.status(),
            "running": self.running,
            "interval_ms": self.interval_ms,
        }

    async def _run(self) -> None:
        while True:
            frame = self.engine.next_frame()
            await publish_demo_frame(frame)
            await asyncio.sleep(self.interval_ms / 1_000.0)

    def start(self, interval_ms: int) -> dict[str, object]:
        self.interval_ms = interval_ms
        if not self.running:
            self._task = asyncio.create_task(self._run())
        return self.status()

    async def stop(self) -> dict[str, object]:
        task = self._task
        self._task = None
        if task is not None and not task.done():
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task
        return self.status()

    async def reset(self) -> dict[str, object]:
        await self.stop()
        self.engine.reset()
        return self.status()


demo_engine = DemoEngine()
demo_controller = DemoController(demo_engine)


@router.get("/status")
def demo_status() -> dict[str, object]:
    return demo_controller.status()


@router.post("/start")
def demo_start(interval_ms: int = Query(default=1_000, ge=100, le=60_000)) -> dict[str, object]:
    return demo_controller.start(interval_ms)


@router.post("/stop")
async def demo_stop() -> dict[str, object]:
    return await demo_controller.stop()


@router.post("/reset")
async def demo_reset() -> dict[str, object]:
    return await demo_controller.reset()


@router.post("/tick")
async def demo_tick() -> dict[str, object]:
    frame = demo_engine.next_frame()
    await publish_demo_frame(frame)
    return frame
