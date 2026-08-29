from __future__ import annotations

from pydantic import BaseModel


class PnLAttributionInput(BaseModel):
    model_edge: float = 0.0
    market_movement: float = 0.0
    execution: float = 0.0
    spread_capture: float = 0.0
    slippage: float = 0.0
    fees: float = 0.0
    hedging: float = 0.0
    timing: float = 0.0
    observed_total_pnl: float = 0.0


class PnLAttribution(BaseModel):
    model_edge: float
    market_movement: float
    execution: float
    spread_capture: float
    slippage: float
    fees: float
    hedging: float
    timing: float
    residual: float
    attributed_total: float
    observed_total_pnl: float


def attribute_pnl(data: PnLAttributionInput) -> PnLAttribution:
    attributed = (
        data.model_edge
        + data.market_movement
        + data.execution
        + data.spread_capture
        + data.slippage
        + data.fees
        + data.hedging
        + data.timing
    )
    residual = data.observed_total_pnl - attributed
    return PnLAttribution(
        model_edge=data.model_edge,
        market_movement=data.market_movement,
        execution=data.execution,
        spread_capture=data.spread_capture,
        slippage=data.slippage,
        fees=data.fees,
        hedging=data.hedging,
        timing=data.timing,
        residual=residual,
        attributed_total=attributed + residual,
        observed_total_pnl=data.observed_total_pnl,
    )
