from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Response
from pydantic import BaseModel, Field

from .calibration import (
    CalibrationObservation,
    brier_score,
    calibration_error,
    log_loss,
)
from .demo import router as demo_router
from .models import MarketSnapshot
from .observability import RuntimeMetrics
from .replay import HistoricalReplay, ReplayFrame

router = APIRouter(prefix="/research", tags=["research"])
router.include_router(demo_router)
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


def _prometheus_escape(value: str) -> str:
    return value.replace("\\", "\\\\").replace("\n", "\\n").replace('"', '\\"')


def _prometheus_metrics(snapshot: dict[str, object]) -> str:
    counters = snapshot["counters"]
    http_by_path = snapshot["http_by_path"]
    http_by_status = snapshot["http_by_status"]
    assert isinstance(counters, dict)
    assert isinstance(http_by_path, dict)
    assert isinstance(http_by_status, dict)

    lines = [
        "# HELP proto_simulation_latency_ms Average simulated execution latency in milliseconds.",
        "# TYPE proto_simulation_latency_ms gauge",
        f"proto_simulation_latency_ms {float(snapshot['average_simulation_latency_ms'])}",
        "# HELP proto_simulation_latency_samples Number of simulated latency observations.",
        "# TYPE proto_simulation_latency_samples counter",
        f"proto_simulation_latency_samples {int(snapshot['latency_samples'])}",
        "# HELP proto_http_requests_total Total HTTP requests observed by the API.",
        "# TYPE proto_http_requests_total counter",
        f"proto_http_requests_total {int(snapshot['http_request_count'])}",
        "# HELP proto_http_errors_total Total HTTP 5xx responses observed by the API.",
        "# TYPE proto_http_errors_total counter",
        f"proto_http_errors_total {int(snapshot['http_error_count'])}",
        "# HELP proto_http_latency_ms Average HTTP request latency in milliseconds.",
        "# TYPE proto_http_latency_ms gauge",
        f"proto_http_latency_ms {float(snapshot['average_http_latency_ms'])}",
        "# HELP proto_runtime_events_total Runtime event counters.",
        "# TYPE proto_runtime_events_total counter",
    ]
    for name, count in sorted(counters.items()):
        lines.append(
            f'proto_runtime_events_total{{event="{_prometheus_escape(str(name))}"}} {int(count)}'
        )

    lines.extend(
        [
            "# HELP proto_http_path_requests_total HTTP requests grouped by API path.",
            "# TYPE proto_http_path_requests_total counter",
        ]
    )
    for path, count in sorted(http_by_path.items()):
        lines.append(
            f'proto_http_path_requests_total{{path="{_prometheus_escape(str(path))}"}} {int(count)}'
        )

    lines.extend(
        [
            "# HELP proto_http_status_total HTTP requests grouped by response status.",
            "# TYPE proto_http_status_total counter",
        ]
    )
    for status, count in sorted(http_by_status.items()):
        lines.append(f'proto_http_status_total{{status="{status}"}} {int(count)}')

    return "\n".join(lines) + "\n"


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


@router.get("/observability/snapshot")
def observability_snapshot() -> dict[str, object]:
    return {
        "scope": "SIMULATION_REPLAY_ONLY",
        "real_money_execution": False,
        **metrics.snapshot(),
    }


@router.get("/observability/prometheus")
def prometheus_metrics() -> Response:
    return Response(
        content=_prometheus_metrics(metrics.snapshot()),
        media_type="text/plain; version=0.0.4; charset=utf-8",
    )
