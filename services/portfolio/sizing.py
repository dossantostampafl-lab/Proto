from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, Field


class SizingMethod(StrEnum):
    FIXED_FRACTIONAL = "FIXED_FRACTIONAL"
    VOLATILITY_ADJUSTED = "VOLATILITY_ADJUSTED"
    EDGE_ADJUSTED = "EDGE_ADJUSTED"
    CAPPED_KELLY_RESEARCH_ONLY = "CAPPED_KELLY_RESEARCH_ONLY"


class SizingInput(BaseModel):
    capital: float = Field(gt=0, allow_inf_nan=False)
    max_fraction: float = Field(default=0.02, gt=0, le=0.10, allow_inf_nan=False)
    volatility: float = Field(default=0.20, ge=0, allow_inf_nan=False)
    target_volatility: float = Field(default=0.15, gt=0, allow_inf_nan=False)
    net_edge: float = Field(default=0.0, ge=-1.0, le=1.0, allow_inf_nan=False)
    confidence: float = Field(default=0.5, ge=0.0, le=1.0, allow_inf_nan=False)
    market_probability: float = Field(
        default=0.5,
        gt=0.0,
        lt=1.0,
        allow_inf_nan=False,
    )
    model_probability: float = Field(
        default=0.5,
        ge=0.0,
        le=1.0,
        allow_inf_nan=False,
    )
    hard_notional_cap: float = Field(gt=0, allow_inf_nan=False)


class SizingResult(BaseModel):
    method: SizingMethod
    fraction: float
    notional: float
    capped: bool


def size_position(method: SizingMethod, data: SizingInput) -> SizingResult:
    base = data.max_fraction

    if method == SizingMethod.FIXED_FRACTIONAL:
        fraction = base
    elif method == SizingMethod.VOLATILITY_ADJUSTED:
        vol_scale = min(data.target_volatility / max(data.volatility, 1e-9), 1.0)
        fraction = base * vol_scale
    elif method == SizingMethod.EDGE_ADJUSTED:
        edge_scale = min(max(data.net_edge, 0.0) / 0.10, 1.0)
        fraction = base * edge_scale * data.confidence
    else:
        p = data.model_probability
        q = 1.0 - p
        price = data.market_probability
        b = (1.0 - price) / price
        raw_kelly = max((b * p - q) / max(b, 1e-9), 0.0)
        fraction = min(raw_kelly * 0.10, base)

    fraction = max(min(fraction, data.max_fraction), 0.0)
    raw_notional = data.capital * fraction
    notional = min(raw_notional, data.hard_notional_cap)
    return SizingResult(
        method=method,
        fraction=fraction,
        notional=notional,
        capped=notional < raw_notional,
    )
