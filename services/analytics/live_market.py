from __future__ import annotations

from dataclasses import dataclass
from math import log, sqrt
from statistics import fmean

from services.market_data.core import MarketTick, compute_orderbook_metrics


@dataclass(frozen=True, slots=True)
class LiveMarketAnalytics:
    symbol: str
    sample_count: int
    first_mid: float
    last_mid: float
    simple_return: float
    log_return: float
    realized_volatility: float
    average_spread_bps: float
    current_spread_bps: float
    current_imbalance: float
    current_microprice: float
    observation_span_seconds: float


def _spread_bps(tick: MarketTick) -> float:
    if tick.mid <= 0:
        return 0.0
    return tick.spread / tick.mid * 10_000.0


def calculate_live_market_analytics(ticks: list[MarketTick]) -> LiveMarketAnalytics:
    if not ticks:
        raise ValueError("at least one market tick is required")
    symbols = {tick.symbol for tick in ticks}
    if len(symbols) != 1:
        raise ValueError("all market ticks must have the same symbol")

    ordered = sorted(ticks, key=lambda tick: (tick.timestamp, tick.sequence))
    mids = [tick.mid for tick in ordered]
    log_returns = [log(current / previous) for previous, current in zip(mids, mids[1:])]
    realized_volatility = sqrt(sum(value * value for value in log_returns))
    first_mid = mids[0]
    last_mid = mids[-1]
    current_book = compute_orderbook_metrics(ordered[-1])

    return LiveMarketAnalytics(
        symbol=ordered[-1].symbol,
        sample_count=len(ordered),
        first_mid=first_mid,
        last_mid=last_mid,
        simple_return=(last_mid / first_mid) - 1.0,
        log_return=log(last_mid / first_mid),
        realized_volatility=realized_volatility,
        average_spread_bps=fmean(_spread_bps(tick) for tick in ordered),
        current_spread_bps=_spread_bps(ordered[-1]),
        current_imbalance=current_book.imbalance,
        current_microprice=current_book.microprice,
        observation_span_seconds=max(
            (ordered[-1].timestamp - ordered[0].timestamp).total_seconds(),
            0.0,
        ),
    )
