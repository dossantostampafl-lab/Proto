from __future__ import annotations

from collections.abc import Iterable
from datetime import timedelta
from enum import StrEnum
from math import log, sqrt

from pydantic import BaseModel, ConfigDict

from services.market_data.core import MarketTick, compute_orderbook_metrics


class FeatureWindow(StrEnum):
    S1 = "1s"
    S5 = "5s"
    S15 = "15s"
    S30 = "30s"
    M1 = "1m"
    M5 = "5m"
    M15 = "15m"

    @property
    def seconds(self) -> int:
        return {
            FeatureWindow.S1: 1,
            FeatureWindow.S5: 5,
            FeatureWindow.S15: 15,
            FeatureWindow.S30: 30,
            FeatureWindow.M1: 60,
            FeatureWindow.M5: 300,
            FeatureWindow.M15: 900,
        }[self]


class FeatureFrame(BaseModel):
    model_config = ConfigDict(frozen=True)

    symbol: str
    window: FeatureWindow
    sample_count: int
    return_simple: float
    log_return: float
    realized_volatility: float
    momentum: float
    spread: float
    orderbook_imbalance: float
    microprice_deviation: float
    volume_acceleration: float
    price_velocity: float
    liquidity_score: float


def _window_ticks(ticks: Iterable[MarketTick], window: FeatureWindow) -> list[MarketTick]:
    ordered = sorted(ticks, key=lambda item: (item.timestamp, item.sequence))
    if not ordered:
        return []
    cutoff = ordered[-1].timestamp - timedelta(seconds=window.seconds)
    return [tick for tick in ordered if tick.timestamp >= cutoff]


def _realized_volatility(prices: list[float]) -> float:
    if len(prices) < 3:
        return 0.0
    returns = [log(prices[index] / prices[index - 1]) for index in range(1, len(prices))]
    mean = sum(returns) / len(returns)
    variance = sum((value - mean) ** 2 for value in returns) / len(returns)
    return sqrt(max(variance, 0.0))


def build_feature_frame(
    ticks: Iterable[MarketTick],
    *,
    window: FeatureWindow,
) -> FeatureFrame:
    sample = _window_ticks(ticks, window)
    if not sample:
        raise ValueError("feature window requires at least one market tick")

    first = sample[0]
    last = sample[-1]
    prices = [tick.mid for tick in sample]
    first_price = max(first.mid, 1e-12)
    last_price = max(last.mid, 1e-12)
    elapsed = max((last.timestamp - first.timestamp).total_seconds(), 1e-9)

    metrics = compute_orderbook_metrics(last)
    simple_return = last_price / first_price - 1.0
    log_return = log(last_price / first_price)
    volume_delta = last.volume - first.volume
    volume_acceleration = volume_delta / elapsed
    price_velocity = (last_price - first_price) / elapsed
    microprice_deviation = (metrics.microprice - metrics.mid_price) / max(metrics.mid_price, 1e-12)
    normalized_spread = max(metrics.spread, 0.0) / max(metrics.mid_price, 1e-12)
    liquidity_score = metrics.depth / (1.0 + 10_000.0 * normalized_spread)

    return FeatureFrame(
        symbol=last.symbol,
        window=window,
        sample_count=len(sample),
        return_simple=simple_return,
        log_return=log_return,
        realized_volatility=_realized_volatility(prices),
        momentum=last_price - first_price,
        spread=metrics.spread,
        orderbook_imbalance=metrics.imbalance,
        microprice_deviation=microprice_deviation,
        volume_acceleration=volume_acceleration,
        price_velocity=price_velocity,
        liquidity_score=liquidity_score,
    )
