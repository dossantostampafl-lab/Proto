from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from services.market_data.core import MarketTick, compute_orderbook_metrics


class MicrostructureSnapshot(BaseModel):
    model_config = ConfigDict(frozen=True)

    symbol: str
    order_flow_imbalance: float = Field(allow_inf_nan=False)
    normalized_ofi: float = Field(ge=-1.0, le=1.0, allow_inf_nan=False)
    spread_bps: float = Field(ge=0.0, allow_inf_nan=False)
    microprice_deviation: float = Field(allow_inf_nan=False)
    total_depth: float = Field(ge=0.0, allow_inf_nan=False)
    liquidity_score: float = Field(ge=0.0, allow_inf_nan=False)


def _bid_flow(previous: MarketTick, current: MarketTick) -> float:
    if current.bid > previous.bid:
        return current.bid_size
    if current.bid == previous.bid:
        return current.bid_size - previous.bid_size
    return -previous.bid_size


def _ask_flow(previous: MarketTick, current: MarketTick) -> float:
    if current.ask < previous.ask:
        return current.ask_size
    if current.ask == previous.ask:
        return current.ask_size - previous.ask_size
    return -previous.ask_size


def calculate_order_flow_imbalance(previous: MarketTick, current: MarketTick) -> float:
    if previous.symbol != current.symbol or previous.venue != current.venue:
        raise ValueError("OFI requires ticks from the same venue and symbol")
    if current.sequence <= previous.sequence:
        raise ValueError("OFI requires a strictly increasing sequence")
    return _bid_flow(previous, current) - _ask_flow(previous, current)


def build_microstructure_snapshot(
    previous: MarketTick,
    current: MarketTick,
) -> MicrostructureSnapshot:
    ofi = calculate_order_flow_imbalance(previous, current)
    metrics = compute_orderbook_metrics(current)
    depth = max(metrics.depth, 0.0)
    normalized_ofi = ofi / depth if depth > 0.0 else 0.0
    normalized_ofi = max(-1.0, min(1.0, normalized_ofi))
    mid = max(metrics.mid_price, 1e-12)
    spread_bps = max(metrics.spread, 0.0) / mid * 10_000.0
    microprice_deviation = (metrics.microprice - metrics.mid_price) / mid
    liquidity_score = depth / (1.0 + spread_bps)

    return MicrostructureSnapshot(
        symbol=current.symbol,
        order_flow_imbalance=ofi,
        normalized_ofi=normalized_ofi,
        spread_bps=spread_bps,
        microprice_deviation=microprice_deviation,
        total_depth=depth,
        liquidity_score=liquidity_score,
    )
