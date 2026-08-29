from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter
from fastapi.responses import PlainTextResponse
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
from .surface import router as surface_router

router = APIRouter(tags=["research"])
router.include_router(surface_router)
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


@router.post("/research/calibration")
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


@router.post("/research/replay")
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


@router.get("/research/metrics")
def runtime_metrics() -> dict[str, object]:
    return metrics.snapshot()


@router.get("/metrics/prometheus", response_class=PlainTextResponse)
def prometheus_metrics() -> str:
    snapshot = metrics.snapshot()
    counters = snapshot["counters"]
    lines = [
        "# HELP proto_http_requests_total Total observed HTTP requests.",
        "# TYPE proto_http_requests_total counter",
        f"proto_http_requests_total {snapshot['http_request_count']}",
        "# HELP proto_http_errors_total Total observed HTTP 5xx responses.",
        "# TYPE proto_http_errors_total counter",
        f"proto_http_errors_total {snapshot['http_error_count']}",
        "# HELP proto_http_latency_ms Average HTTP request latency in milliseconds.",
        "# TYPE proto_http_latency_ms gauge",
        f"proto_http_latency_ms {snapshot['average_http_latency_ms']}",
        "# HELP proto_simulation_latency_ms Average simulation latency in milliseconds.",
        "# TYPE proto_simulation_latency_ms gauge",
        f"proto_simulation_latency_ms {snapshot['average_simulation_latency_ms']}",
    ]
    for name, value in sorted(counters.items()):
        safe_name = "".join(character if character.isalnum() else "_" for character in name)
        lines.extend(
            [
                f"# TYPE proto_{safe_name}_total counter",
                f"proto_{safe_name}_total {value}",
            ]
        )
    return "\n".join(lines) + "\n"


@router.post("/research/metrics/reset")
def reset_runtime_metrics() -> dict[str, object]:
    metrics.reset()
    return metrics.snapshot()
