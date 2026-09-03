from __future__ import annotations

from enum import StrEnum
from math import isfinite

from pydantic import BaseModel, ConfigDict, Field, model_validator


class TrendRegime(StrEnum):
    STRONG_DOWN = "STRONG_DOWN"
    DOWN = "DOWN"
    NEUTRAL = "NEUTRAL"
    UP = "UP"
    STRONG_UP = "STRONG_UP"


class VolatilityRegime(StrEnum):
    LOW = "LOW"
    NORMAL = "NORMAL"
    HIGH = "HIGH"


class RegimePolicy(BaseModel):
    """Explicit classification thresholds supplied by a versioned policy."""

    model_config = ConfigDict(frozen=True, allow_inf_nan=False)

    trend_threshold: float = Field(gt=0)
    strong_trend_threshold: float = Field(gt=0)
    low_volatility_threshold: float = Field(ge=0)
    high_volatility_threshold: float = Field(gt=0)

    @model_validator(mode="after")
    def validate_ordering(self) -> RegimePolicy:
        if self.strong_trend_threshold <= self.trend_threshold:
            raise ValueError("strong_trend_threshold must exceed trend_threshold")
        if self.high_volatility_threshold <= self.low_volatility_threshold:
            raise ValueError(
                "high_volatility_threshold must exceed low_volatility_threshold"
            )
        return self


class MarketIntelligenceInput(BaseModel):
    model_config = ConfigDict(frozen=True, allow_inf_nan=False)

    instrument_id: str = Field(min_length=3, max_length=160)
    return_signal: float
    realized_volatility: float = Field(ge=0)
    liquidity_score: float = Field(ge=0, le=1)
    confidence: float | None = Field(default=None, ge=0, le=1)
    net_edge: float | None = None
    calibration_quality: float | None = Field(default=None, ge=0, le=1)
    risk_quality: float | None = Field(default=None, ge=0, le=1)
    provenance_complete: bool

    @model_validator(mode="after")
    def validate_instrument(self) -> MarketIntelligenceInput:
        if ":" not in self.instrument_id:
            raise ValueError("instrument_id must be namespaced")
        return self


class MarketState(BaseModel):
    model_config = ConfigDict(frozen=True, allow_inf_nan=False)

    instrument_id: str
    trend_regime: TrendRegime
    volatility_regime: VolatilityRegime
    return_signal: float
    realized_volatility: float
    liquidity_score: float
    confidence: float | None
    net_edge: float | None
    calibration_quality: float | None
    risk_quality: float | None
    provenance_complete: bool


def classify_market_state(
    observation: MarketIntelligenceInput,
    policy: RegimePolicy,
) -> MarketState:
    signal = observation.return_signal
    if signal >= policy.strong_trend_threshold:
        trend = TrendRegime.STRONG_UP
    elif signal >= policy.trend_threshold:
        trend = TrendRegime.UP
    elif signal <= -policy.strong_trend_threshold:
        trend = TrendRegime.STRONG_DOWN
    elif signal <= -policy.trend_threshold:
        trend = TrendRegime.DOWN
    else:
        trend = TrendRegime.NEUTRAL

    volatility = observation.realized_volatility
    if volatility <= policy.low_volatility_threshold:
        vol_regime = VolatilityRegime.LOW
    elif volatility >= policy.high_volatility_threshold:
        vol_regime = VolatilityRegime.HIGH
    else:
        vol_regime = VolatilityRegime.NORMAL

    return MarketState(
        instrument_id=observation.instrument_id.strip().upper(),
        trend_regime=trend,
        volatility_regime=vol_regime,
        return_signal=signal,
        realized_volatility=volatility,
        liquidity_score=observation.liquidity_score,
        confidence=observation.confidence,
        net_edge=observation.net_edge,
        calibration_quality=observation.calibration_quality,
        risk_quality=observation.risk_quality,
        provenance_complete=observation.provenance_complete,
    )


class OpportunityPolicy(BaseModel):
    """Ranking policy with no hidden/default weights or thresholds."""

    model_config = ConfigDict(frozen=True, allow_inf_nan=False)

    minimum_liquidity: float = Field(ge=0, le=1)
    minimum_confidence: float = Field(ge=0, le=1)
    minimum_net_edge: float = Field(ge=0)
    minimum_calibration_quality: float = Field(ge=0, le=1)
    minimum_risk_quality: float = Field(ge=0, le=1)
    edge_scale: float = Field(gt=0)
    weight_edge: float = Field(ge=0)
    weight_confidence: float = Field(ge=0)
    weight_liquidity: float = Field(ge=0)
    weight_calibration: float = Field(ge=0)
    weight_risk: float = Field(ge=0)

    @model_validator(mode="after")
    def validate_weights(self) -> OpportunityPolicy:
        total = (
            self.weight_edge
            + self.weight_confidence
            + self.weight_liquidity
            + self.weight_calibration
            + self.weight_risk
        )
        if not isfinite(total) or total <= 0:
            raise ValueError("opportunity weights must have a positive finite sum")
        return self


class Opportunity(BaseModel):
    model_config = ConfigDict(frozen=True, allow_inf_nan=False)

    instrument_id: str
    score: float = Field(ge=0, le=1)
    net_edge: float
    confidence: float
    liquidity_score: float
    calibration_quality: float
    risk_quality: float
    trend_regime: TrendRegime
    volatility_regime: VolatilityRegime


def rank_opportunities(
    states: list[MarketState],
    policy: OpportunityPolicy,
    *,
    limit: int = 15,
) -> list[Opportunity]:
    if limit < 1:
        raise ValueError("limit must be >= 1")
    total_weight = (
        policy.weight_edge
        + policy.weight_confidence
        + policy.weight_liquidity
        + policy.weight_calibration
        + policy.weight_risk
    )
    ranked: list[Opportunity] = []
    for state in states:
        if not state.provenance_complete:
            continue
        if (
            state.net_edge is None
            or state.confidence is None
            or state.calibration_quality is None
            or state.risk_quality is None
        ):
            continue
        if (
            state.net_edge < policy.minimum_net_edge
            or state.confidence < policy.minimum_confidence
            or state.liquidity_score < policy.minimum_liquidity
            or state.calibration_quality < policy.minimum_calibration_quality
            or state.risk_quality < policy.minimum_risk_quality
        ):
            continue

        normalized_edge = min(max(state.net_edge / policy.edge_scale, 0.0), 1.0)
        score = (
            normalized_edge * policy.weight_edge
            + state.confidence * policy.weight_confidence
            + state.liquidity_score * policy.weight_liquidity
            + state.calibration_quality * policy.weight_calibration
            + state.risk_quality * policy.weight_risk
        ) / total_weight
        ranked.append(
            Opportunity(
                instrument_id=state.instrument_id,
                score=score,
                net_edge=state.net_edge,
                confidence=state.confidence,
                liquidity_score=state.liquidity_score,
                calibration_quality=state.calibration_quality,
                risk_quality=state.risk_quality,
                trend_regime=state.trend_regime,
                volatility_regime=state.volatility_regime,
            )
        )

    ranked.sort(key=lambda item: (-item.score, item.instrument_id))
    return ranked[:limit]
