from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter
from pydantic import BaseModel, Field

from services.quant.synthetic_greeks import BinaryContractInputs, synthetic_greeks

from .calibration import (
    CalibrationObservation,
    brier_score,
    calibration_error,
    log_loss,
)
from .models import MarketSnapshot
from .observability import RuntimeMetrics
from .replay import HistoricalReplay, ReplayFrame

router = APIRouter(prefix="/research", tags=["research"])
metrics = RuntimeMetrics()


class CalibrationPoint(BaseModel):
    probability: float = Field(ge=0.0, le=1.0)
    outcome: int = Field(ge=0, le=1)


class CalibrationRequest(BaseModel):
    observations: list[CalibrationPoint] = Field(min_length=1)
    bins: int = Field(default=10, ge=2, le=100)


class ReplayPoint(BaseModel):
    timestamp: datetime
    snapshot: MarketSnapshot


class ReplayRequest(BaseModel):
    frames: list[ReplayPoint] = Field(min_length=1)


class SyntheticGreeksRequest(BaseModel):
    spot: float = Field(gt=0.0)
    strike: float = Field(gt=0.0)
    volatility: float = Field(gt=0.0, le=10.0)
    time_to_expiry_years: float = Field(gt=0.0, le=100.0)


@router.post("/calibration")
def calibration(request: CalibrationRequest) -> dict[str, float | int]:
    observations = [
        CalibrationObservation(probability=item.probability, outcome=item.outcome)
        for item in request.observations
    ]
    metrics.increment("calibration_requests")
    return {
        "count": len(observations),
        "brier_score": round(brier_score(observations), 12),
        "log_loss": round(log_loss(observations), 12),
        "expected_calibration_error": round(
            calibration_error(observations, request.bins),
            12,
        ),
    }


@router.post("/synthetic-greeks")
def synthetic_greeks_endpoint(request: SyntheticGreeksRequest) -> dict[str, float | str]:
    result = synthetic_greeks(
        BinaryContractInputs(
            spot=request.spot,
            strike=request.strike,
            volatility=request.volatility,
            time_to_expiry_years=request.time_to_expiry_years,
        )
    )
    metrics.increment("synthetic_greeks_requests")
    return {
        "mode": "RESEARCH_ONLY",
        "fair_probability": round(result.fair_probability, 12),
        "delta": round(result.delta, 12),
        "gamma": round(result.gamma, 12),
        "vega": round(result.vega, 12),
        "theta_per_year": round(result.theta_per_year, 12),
    }


@router.post("/replay")
def replay(request: ReplayRequest) -> dict[str, object]:
    engine = HistoricalReplay(
        [
            ReplayFrame(timestamp=item.timestamp, snapshot=item.snapshot)
            for item in request.frames
        ]
    )
    frames = engine.run_all()
    metrics.increment("historical_replay_requests")
    metrics.increment("historical_replay_frames", len(frames))
    return {
        "mode": "HISTORICAL_REPLAY",
        "total_frames": engine.total_frames,
        "processed_frames": len(frames),
        "finished": engine.finished,
        "frames": [
            {
                "timestamp": frame.timestamp,
                "market_id": frame.snapshot.market_id,
                "symbol": frame.snapshot.symbol,
                "bid": frame.snapshot.bid,
                "ask": frame.snapshot.ask,
                "market_probability": frame.snapshot.market_probability,
            }
            for frame in frames
        ],
    }


@router.get("/metrics")
def runtime_metrics() -> dict[str, object]:
    return metrics.snapshot()


@router.post("/metrics/reset")
def reset_runtime_metrics() -> dict[str, object]:
    metrics.reset()
    return metrics.snapshot()
