from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter
from pydantic import BaseModel, Field

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
