from __future__ import annotations

from enum import StrEnum
from math import isfinite

from pydantic import BaseModel, ConfigDict, Field, model_validator


class TrendState(StrEnum):
    STRONG_BEAR = "STRONG_BEAR"
    BEAR = "BEAR"
    NEUTRAL = "NEUTRAL"
    BULL = "BULL"
    STRONG_BULL = "STRONG_BULL"


class TradeDirection(StrEnum):
    LONG = "LONG"
    SHORT = "SHORT"


class TimeframeSnapshot(BaseModel):
    model_config = ConfigDict(frozen=True)

    close: float = Field(gt=0.0, allow_inf_nan=False)
    ema9: float = Field(gt=0.0, allow_inf_nan=False)
    ema21: float = Field(gt=0.0, allow_inf_nan=False)
    ema50: float = Field(gt=0.0, allow_inf_nan=False)
    ema9_slope: float = Field(allow_inf_nan=False)
    ema21_slope: float = Field(allow_inf_nan=False)
    ema50_slope: float = Field(allow_inf_nan=False)
    structure_score: float = Field(ge=-1.0, le=1.0, allow_inf_nan=False)
    atr: float = Field(ge=0.0, allow_inf_nan=False)


class SetupSnapshot(BaseModel):
    model_config = ConfigDict(frozen=True)

    pullback_quality: float = Field(ge=0.0, le=1.0, allow_inf_nan=False)
    structure_quality: float = Field(ge=0.0, le=1.0, allow_inf_nan=False)
    trigger_quality: float = Field(ge=0.0, le=1.0, allow_inf_nan=False)
    volume_zscore: float = Field(default=0.0, allow_inf_nan=False)


class RiskSnapshot(BaseModel):
    model_config = ConfigDict(frozen=True)

    nav: float = Field(gt=0.0, allow_inf_nan=False)
    entry: float = Field(gt=0.0, allow_inf_nan=False)
    structural_invalidation: float = Field(gt=0.0, allow_inf_nan=False)
    atr: float = Field(ge=0.0, allow_inf_nan=False)
    atr_buffer_multiple: float = Field(default=0.5, ge=0.0, le=10.0, allow_inf_nan=False)
    risk_fraction: float = Field(default=0.005, gt=0.0, le=0.02, allow_inf_nan=False)
    cluster_risk: float = Field(default=0.0, ge=0.0, le=1.0, allow_inf_nan=False)
    max_cluster_risk: float = Field(default=0.03, gt=0.0, le=1.0, allow_inf_nan=False)
    portfolio_drawdown: float = Field(default=0.0, ge=0.0, le=1.0, allow_inf_nan=False)
    max_portfolio_drawdown: float = Field(default=0.15, gt=0.0, le=1.0, allow_inf_nan=False)


class ExpectancySnapshot(BaseModel):
    model_config = ConfigDict(frozen=True)

    win_probability: float = Field(ge=0.0, le=1.0, allow_inf_nan=False)
    average_win_r: float = Field(gt=0.0, allow_inf_nan=False)
    average_loss_r: float = Field(gt=0.0, allow_inf_nan=False)
    costs_r: float = Field(default=0.0, ge=0.0, allow_inf_nan=False)

    @property
    def expectancy_r(self) -> float:
        return (
            self.win_probability * self.average_win_r
            - (1.0 - self.win_probability) * self.average_loss_r
            - self.costs_r
        )


class HierarchicalTrendInput(BaseModel):
    model_config = ConfigDict(frozen=True)

    direction: TradeDirection
    higher: TimeframeSnapshot
    middle: TimeframeSnapshot
    lower: TimeframeSnapshot
    setup: SetupSnapshot
    risk: RiskSnapshot
    expectancy: ExpectancySnapshot
    minimum_regime_score: float = Field(default=0.25, ge=0.0, le=1.0)
    minimum_alignment_score: float = Field(default=0.55, ge=0.0, le=1.0)
    minimum_setup_score: float = Field(default=0.60, ge=0.0, le=1.0)
    minimum_expectancy_r: float = Field(default=0.0, allow_inf_nan=False)

    @model_validator(mode="after")
    def validate_geometry(self) -> HierarchicalTrendInput:
        if self.direction is TradeDirection.LONG and self.risk.structural_invalidation >= self.risk.entry:
            raise ValueError("long structural invalidation must be below entry")
        if self.direction is TradeDirection.SHORT and self.risk.structural_invalidation <= self.risk.entry:
            raise ValueError("short structural invalidation must be above entry")
        return self


class HierarchicalTrendResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    regime_score: float = Field(ge=-1.0, le=1.0, allow_inf_nan=False)
    regime_state: TrendState
    alignment_score: float = Field(ge=0.0, le=1.0, allow_inf_nan=False)
    setup_score: float = Field(ge=0.0, le=1.0, allow_inf_nan=False)
    stop_price: float = Field(gt=0.0, allow_inf_nan=False)
    stop_distance: float = Field(gt=0.0, allow_inf_nan=False)
    position_units: float = Field(ge=0.0, allow_inf_nan=False)
    capital_at_risk: float = Field(ge=0.0, allow_inf_nan=False)
    expectancy_r: float = Field(allow_inf_nan=False)
    decision: str
    rejection_reasons: tuple[str, ...]


def _bounded_slope(value: float) -> float:
    if not isfinite(value):
        raise ValueError("slope must be finite")
    return max(-1.0, min(1.0, value))


def _timeframe_score(snapshot: TimeframeSnapshot) -> float:
    price_stack = 0.0
    if snapshot.close > snapshot.ema9 > snapshot.ema21 > snapshot.ema50:
        price_stack = 1.0
    elif snapshot.close < snapshot.ema9 < snapshot.ema21 < snapshot.ema50:
        price_stack = -1.0

    slope_score = (
        _bounded_slope(snapshot.ema9_slope)
        + _bounded_slope(snapshot.ema21_slope)
        + _bounded_slope(snapshot.ema50_slope)
    ) / 3.0
    score = 0.45 * snapshot.structure_score + 0.35 * slope_score + 0.20 * price_stack
    return max(-1.0, min(1.0, score))


def _state(score: float) -> TrendState:
    if score >= 0.70:
        return TrendState.STRONG_BULL
    if score >= 0.25:
        return TrendState.BULL
    if score <= -0.70:
        return TrendState.STRONG_BEAR
    if score <= -0.25:
        return TrendState.BEAR
    return TrendState.NEUTRAL


def _directional_alignment(direction: TradeDirection, scores: tuple[float, float, float]) -> float:
    sign = 1.0 if direction is TradeDirection.LONG else -1.0
    directional = [max(0.0, min(1.0, 0.5 + 0.5 * sign * score)) for score in scores]
    # Higher timeframe is intentionally dominant; lower timeframe is timing, not regime authority.
    return 0.50 * directional[0] + 0.30 * directional[1] + 0.20 * directional[2]


def _setup_score(setup: SetupSnapshot) -> float:
    volume_confirmation = max(0.0, min(1.0, 0.5 + setup.volume_zscore / 4.0))
    return (
        0.35 * setup.pullback_quality
        + 0.30 * setup.structure_quality
        + 0.25 * setup.trigger_quality
        + 0.10 * volume_confirmation
    )


def _stop_price(direction: TradeDirection, risk: RiskSnapshot) -> float:
    buffer = risk.atr * risk.atr_buffer_multiple
    if direction is TradeDirection.LONG:
        return risk.structural_invalidation - buffer
    return risk.structural_invalidation + buffer


def evaluate_hierarchical_trend(data: HierarchicalTrendInput) -> HierarchicalTrendResult:
    higher_score = _timeframe_score(data.higher)
    middle_score = _timeframe_score(data.middle)
    lower_score = _timeframe_score(data.lower)
    sign = 1.0 if data.direction is TradeDirection.LONG else -1.0

    alignment_score = _directional_alignment(
        data.direction,
        (higher_score, middle_score, lower_score),
    )
    setup_score = _setup_score(data.setup)
    stop_price = _stop_price(data.direction, data.risk)
    stop_distance = abs(data.risk.entry - stop_price)
    capital_at_risk = data.risk.nav * data.risk.risk_fraction
    position_units = capital_at_risk / stop_distance
    expectancy_r = data.expectancy.expectancy_r

    reasons: list[str] = []
    if sign * higher_score < data.minimum_regime_score:
        reasons.append("HIGHER_TIMEFRAME_REGIME_MISALIGNED")
    if alignment_score < data.minimum_alignment_score:
        reasons.append("MULTITIMEFRAME_ALIGNMENT_TOO_WEAK")
    if setup_score < data.minimum_setup_score:
        reasons.append("SETUP_QUALITY_TOO_LOW")
    if expectancy_r <= data.minimum_expectancy_r:
        reasons.append("NON_POSITIVE_EXPECTANCY")
    if data.risk.cluster_risk + data.risk.risk_fraction > data.risk.max_cluster_risk:
        reasons.append("CLUSTER_RISK_LIMIT")
    if data.risk.portfolio_drawdown >= data.risk.max_portfolio_drawdown:
        reasons.append("DRAWDOWN_GATE")

    return HierarchicalTrendResult(
        regime_score=higher_score,
        regime_state=_state(higher_score),
        alignment_score=alignment_score,
        setup_score=setup_score,
        stop_price=stop_price,
        stop_distance=stop_distance,
        position_units=position_units if not reasons else 0.0,
        capital_at_risk=capital_at_risk if not reasons else 0.0,
        expectancy_r=expectancy_r,
        decision="APPROVED" if not reasons else "REJECTED",
        rejection_reasons=tuple(reasons),
    )
