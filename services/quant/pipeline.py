from __future__ import annotations

from datetime import datetime
from hashlib import sha256
from math import isfinite

from pydantic import BaseModel, ConfigDict, Field, model_validator

from services.analytics.greeks import SyntheticGreeks, calculate_synthetic_greeks
from services.hawkes.core import ExponentialHawkesEngine, HawkesEstimate

from .calibration import CalibrationReport, calibration_report
from .core import EdgeBreakdown, ProbabilityEstimate, compute_edge, estimate_probability
from .expected_value import ExpectedValueResult, calculate_expected_value
from .hierarchical_trend import (
    HierarchicalTrendInput,
    HierarchicalTrendResult,
    evaluate_hierarchical_trend,
)


class CalibrationSample(BaseModel):
    model_config = ConfigDict(frozen=True)

    probability: float = Field(ge=0.0, le=1.0, allow_inf_nan=False)
    outcome: int = Field(ge=0, le=1)


class QuantPipelineInput(BaseModel):
    model_config = ConfigDict(frozen=True)

    market_id: str = Field(min_length=1, max_length=120)
    symbol: str = Field(min_length=1, max_length=32)
    observed_at: datetime
    market_probability: float = Field(ge=0.0, le=1.0, allow_inf_nan=False)
    volatility: float = Field(ge=0.0, allow_inf_nan=False)
    imbalance: float = Field(ge=-1.0, le=1.0, allow_inf_nan=False)
    liquidity_score: float = Field(default=1.0, ge=0.0, le=1.0, allow_inf_nan=False)
    fees: float = Field(default=0.001, ge=0.0, allow_inf_nan=False)
    slippage: float = Field(default=0.001, ge=0.0, allow_inf_nan=False)
    spread_cost: float = Field(default=0.0, ge=0.0, allow_inf_nan=False)
    hedge_cost: float = Field(default=0.0, ge=0.0, allow_inf_nan=False)
    latency_penalty: float = Field(default=0.0005, ge=0.0, allow_inf_nan=False)
    minimum_edge: float = Field(default=0.01, ge=0.0, allow_inf_nan=False)
    calibration_samples: tuple[CalibrationSample, ...] = ()
    calibration_bins: int = Field(default=10, ge=2, le=100)
    calibration_prior_strength: float = Field(default=5.0, gt=0.0, allow_inf_nan=False)
    event_times: tuple[float, ...] = ()
    hawkes_mu: float = Field(default=0.2, ge=0.0, allow_inf_nan=False)
    hawkes_alpha: float = Field(default=0.1, ge=0.0, allow_inf_nan=False)
    hawkes_beta: float = Field(default=1.0, gt=0.0, allow_inf_nan=False)
    expiry_at: datetime | None = None
    hierarchical_trend: HierarchicalTrendInput | None = None

    @model_validator(mode="after")
    def validate_replay_clock(self) -> QuantPipelineInput:
        if self.observed_at.tzinfo is None or self.observed_at.utcoffset() is None:
            raise ValueError("observed_at must be timezone-aware")
        if self.expiry_at is not None and (
            self.expiry_at.tzinfo is None or self.expiry_at.utcoffset() is None
        ):
            raise ValueError("expiry_at must be timezone-aware")
        if self.hawkes_alpha >= self.hawkes_beta:
            raise ValueError("hawkes_alpha/hawkes_beta must define a stable process")
        if any(not isfinite(event_time) for event_time in self.event_times):
            raise ValueError("event_times must contain only finite values")
        return self


class TimeExposure(BaseModel):
    model_config = ConfigDict(frozen=True)

    time_to_expiry_seconds: float | None = Field(default=None, allow_inf_nan=False)
    expiry_pressure: float = Field(allow_inf_nan=False)


class QuantPipelineResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    correlation_id: str
    market_id: str
    symbol: str
    observed_at: datetime
    raw_probability: float = Field(ge=0.0, le=1.0, allow_inf_nan=False)
    calibrated_probability: float = Field(ge=0.0, le=1.0, allow_inf_nan=False)
    fair_probability: float = Field(ge=0.0, le=1.0, allow_inf_nan=False)
    confidence: float = Field(ge=0.0, le=1.0, allow_inf_nan=False)
    uncertainty: float = Field(ge=0.0, le=1.0, allow_inf_nan=False)
    calibration_report: CalibrationReport | None
    edge: EdgeBreakdown
    expected_value: ExpectedValueResult
    hawkes: HawkesEstimate
    greeks: SyntheticGreeks
    time_exposure: TimeExposure
    hierarchical_trend: HierarchicalTrendResult | None
    candidate_decision: str
    candidate_rejection_reasons: tuple[str, ...]
    model_version: str
    feature_version: str


def _calibrated_probability(
    raw_probability: float,
    report: CalibrationReport | None,
    *,
    prior_strength: float,
) -> float:
    if report is None:
        return raw_probability

    bucket = next(
        (
            item
            for item in report.bins
            if item.lower <= raw_probability < item.upper
            or (raw_probability == 1.0 and item.upper == 1.0)
        ),
        None,
    )
    if bucket is None or bucket.count == 0 or bucket.observed_frequency is None:
        return raw_probability

    data_weight = bucket.count / (bucket.count + prior_strength)
    calibrated = data_weight * bucket.observed_frequency + (1.0 - data_weight) * raw_probability
    return min(max(calibrated, 0.0), 1.0)


def _time_exposure(observed_at: datetime, expiry_at: datetime | None) -> TimeExposure:
    if expiry_at is None:
        return TimeExposure(time_to_expiry_seconds=None, expiry_pressure=0.0)

    remaining = max((expiry_at - observed_at).total_seconds(), 0.0)
    pressure = 1.0 / (1.0 + remaining / 3_600.0)
    return TimeExposure(time_to_expiry_seconds=remaining, expiry_pressure=pressure)


def _correlation_id(data: QuantPipelineInput, override: str | None) -> str:
    if override is not None:
        normalized = override.strip()
        if not normalized or len(normalized) > 64:
            raise ValueError("correlation_id must contain 1 to 64 characters")
        return normalized
    canonical = data.model_dump_json(exclude_none=False)
    return sha256(canonical.encode("utf-8")).hexdigest()


def _candidate_decision(
    edge: EdgeBreakdown,
    expected_value: ExpectedValueResult,
    trend: HierarchicalTrendResult | None,
) -> tuple[str, tuple[str, ...]]:
    reasons: list[str] = []
    if edge.decision != "APPROVE_CANDIDATE":
        reasons.append("EDGE_BELOW_THRESHOLD")
    if expected_value.risk_adjusted_ev <= 0.0:
        reasons.append("NON_POSITIVE_RISK_ADJUSTED_EV")
    if trend is not None and trend.decision != "APPROVED":
        reasons.extend(f"TREND:{reason}" for reason in trend.rejection_reasons)

    return ("APPROVED" if not reasons else "REJECTED", tuple(reasons))


def run_quant_pipeline(
    data: QuantPipelineInput,
    *,
    correlation_id: str | None = None,
) -> QuantPipelineResult:
    """Run the deterministic research/replay probability-to-edge pipeline.

    The function is pure with respect to external state. Replay callers provide
    the source event timestamp, calibration observations, event times and optional
    hierarchical trend context, so no wall-clock data can leak into the result.
    """
    raw: ProbabilityEstimate = estimate_probability(
        market_probability=data.market_probability,
        volatility=data.volatility,
        imbalance=data.imbalance,
        timestamp=data.observed_at,
    )

    report = (
        calibration_report(
            [(sample.probability, sample.outcome) for sample in data.calibration_samples],
            bin_count=data.calibration_bins,
        )
        if data.calibration_samples
        else None
    )
    calibrated = _calibrated_probability(
        raw.probability,
        report,
        prior_strength=data.calibration_prior_strength,
    )

    calibration_uncertainty = report.expected_calibration_error * 0.5 if report else 0.0
    liquidity_uncertainty = (1.0 - data.liquidity_score) * 0.05
    uncertainty = min(1.0, raw.uncertainty + calibration_uncertainty + liquidity_uncertainty)
    confidence = max(0.0, 1.0 - uncertainty)
    fair_probability = confidence * calibrated + (1.0 - confidence) * data.market_probability

    liquidity_penalty = (1.0 - data.liquidity_score) * 0.01
    uncertainty_penalty = uncertainty * 0.02
    edge = compute_edge(
        model_probability=fair_probability,
        market_probability=data.market_probability,
        fees=data.fees,
        slippage=data.slippage,
        spread_cost=data.spread_cost,
        hedge_cost=data.hedge_cost,
        uncertainty_penalty=uncertainty_penalty,
        latency_penalty=data.latency_penalty,
        liquidity_penalty=liquidity_penalty,
        minimum_edge=data.minimum_edge,
    )

    expected_value = calculate_expected_value(
        win_probability=fair_probability,
        profit_if_win=1.0 - data.market_probability,
        loss_if_lose=data.market_probability,
        fees=data.fees,
        slippage=data.slippage,
        spread_cost=data.spread_cost,
        hedge_cost=data.hedge_cost,
        latency_cost=data.latency_penalty + liquidity_penalty,
        uncertainty_penalty=uncertainty_penalty,
    )

    hawkes_engine = ExponentialHawkesEngine(mu=data.hawkes_mu, alpha=data.hawkes_alpha, beta=data.hawkes_beta)
    observed_timestamp = data.observed_at.timestamp()
    for event_time in sorted(data.event_times):
        if event_time <= observed_timestamp:
            hawkes_engine.record(event_time)
    hawkes = hawkes_engine.estimate(timestamp=observed_timestamp)

    greeks = calculate_synthetic_greeks(
        market_probability=data.market_probability,
        volatility=data.volatility,
        imbalance=data.imbalance,
    )

    trend = evaluate_hierarchical_trend(data.hierarchical_trend) if data.hierarchical_trend else None
    candidate_decision, candidate_rejection_reasons = _candidate_decision(edge, expected_value, trend)

    return QuantPipelineResult(
        correlation_id=_correlation_id(data, correlation_id),
        market_id=data.market_id,
        symbol=data.symbol.upper(),
        observed_at=data.observed_at,
        raw_probability=raw.probability,
        calibrated_probability=calibrated,
        fair_probability=fair_probability,
        confidence=confidence,
        uncertainty=uncertainty,
        calibration_report=report,
        edge=edge,
        expected_value=expected_value,
        hawkes=hawkes,
        greeks=greeks,
        time_exposure=_time_exposure(data.observed_at, data.expiry_at),
        hierarchical_trend=trend,
        candidate_decision=candidate_decision,
        candidate_rejection_reasons=candidate_rejection_reasons,
        model_version=raw.model_version,
        feature_version=raw.feature_version,
    )
