from __future__ import annotations

from datetime import UTC, datetime
from math import exp

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
    net_edge: float
    minimum_edge: float
    decision: str


def _sigmoid(value: float) -> float:
    return 1.0 / (1.0 + exp(-max(min(value, 40.0), -40.0)))


def estimate_probability(*, market_probability: float, volatility: float, imbalance: float) -> ProbabilityEstimate:
    """Deterministic baseline used until fitted calibration artifacts are available.

    This is intentionally conservative: it anchors to the market probability and applies
    bounded microstructure adjustments. It is not represented as a trained production model.
    """
    p = min(max(market_probability, 1e-6), 1.0 - 1e-6)
    market_logit = __import__("math").log(p / (1.0 - p))
    adjustment = 0.35 * max(min(imbalance, 1.0), -1.0) - 0.15 * max(volatility - 0.20, 0.0)
    probability = _sigmoid(market_logit + adjustment)
    uncertainty = min(0.50, 0.08 + max(volatility, 0.0) * 0.35)
    confidence = max(0.0, min(1.0, 1.0 - uncertainty))
    return ProbabilityEstimate(
        probability=probability,
        confidence=confidence,
        uncertainty=uncertainty,
        timestamp=datetime.now(UTC),
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
    minimum_edge: float = 0.01,
) -> EdgeBreakdown:
    raw_edge = model_probability - market_probability
    net_edge = raw_edge - sum(
        [fees, slippage, spread_cost, hedge_cost, uncertainty_penalty, latency_penalty]
    )
    return EdgeBreakdown(
        raw_edge=raw_edge,
        fees=fees,
        slippage=slippage,
        spread_cost=spread_cost,
        hedge_cost=hedge_cost,
        uncertainty_penalty=uncertainty_penalty,
        latency_penalty=latency_penalty,
        net_edge=net_edge,
        minimum_edge=minimum_edge,
        decision="APPROVE_CANDIDATE" if net_edge > minimum_edge else "REJECT",
    )
