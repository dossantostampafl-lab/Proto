from __future__ import annotations

from datetime import UTC, datetime
from math import exp, log

from pydantic import BaseModel, Field


class ProbabilityEstimate(BaseModel):
    probability: float = Field(ge=0.0, le=1.0)
    confidence: float = Field(ge=0.0, le=1.0)
    uncertainty: float = Field(ge=0.0, le=1.0)
    model_version: str = "baseline-logit-v0"
    feature_version: str = "microstructure-v0"
    timestamp: datetime


class EdgeBreakdown(BaseModel):
    raw_edge: float
    fees: float
    slippage: float
    spread_cost: float
    hedge_cost: float
    uncertainty_penalty: float
    latency_penalty: float
    liquidity_penalty: float = 0.0
    net_edge: float
    minimum_edge: float
    decision: str


def _sigmoid(value: float) -> float:
    return 1.0 / (1.0 + exp(-max(min(value, 40.0), -40.0)))


def estimate_probability(
    *,
    market_probability: float,
    volatility: float,
    imbalance: float,
    timestamp: datetime | None = None,
) -> ProbabilityEstimate:
    """Return a conservative deterministic research baseline.

    The estimator anchors to market probability and applies bounded
    microstructure adjustments. Supplying ``timestamp`` makes replay output
    deterministic with respect to the source event clock.
    """
    p = min(max(market_probability, 1e-6), 1.0 - 1e-6)
    market_logit = log(p / (1.0 - p))
    adjustment = 0.35 * max(min(imbalance, 1.0), -1.0) - 0.15 * max(volatility - 0.20, 0.0)
    probability = _sigmoid(market_logit + adjustment)
    uncertainty = min(0.50, 0.08 + max(volatility, 0.0) * 0.35)
    confidence = max(0.0, min(1.0, 1.0 - uncertainty))
    return ProbabilityEstimate(
        probability=probability,
        confidence=confidence,
        uncertainty=uncertainty,
        timestamp=timestamp or datetime.now(UTC),
    )


def compute_edge(
    *,
    model_probability: float,
    market_probability: float,
    fees: float,
    slippage: float,
    spread_cost: float,
    hedge_cost: float,
    uncertainty_penalty: float,
    latency_penalty: float,
    liquidity_penalty: float = 0.0,
    minimum_edge: float = 0.01,
) -> EdgeBreakdown:
    costs = (
        fees,
        slippage,
        spread_cost,
        hedge_cost,
        uncertainty_penalty,
        latency_penalty,
        liquidity_penalty,
    )
    if any(value < 0.0 for value in costs):
        raise ValueError("edge costs and penalties must be non-negative")

    raw_edge = model_probability - market_probability
    net_edge = raw_edge - sum(costs)
    return EdgeBreakdown(
        raw_edge=raw_edge,
        fees=fees,
        slippage=slippage,
        spread_cost=spread_cost,
        hedge_cost=hedge_cost,
        uncertainty_penalty=uncertainty_penalty,
        latency_penalty=latency_penalty,
        liquidity_penalty=liquidity_penalty,
        net_edge=net_edge,
        minimum_edge=minimum_edge,
        decision="APPROVE_CANDIDATE" if net_edge > minimum_edge else "REJECT",
    )
