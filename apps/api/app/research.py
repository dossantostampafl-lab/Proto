from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, HTTPException
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel, Field

from services.quant.calibration import calibration_report
from services.quant.pipeline import (
    CalibrationSample,
    QuantPipelineInput,
    run_quant_pipeline,
)

from .app_state import persistence_engine, portfolio
from .circuit_surface import router as circuit_router
from .data_snooping_surface import router as data_snooping_router
from .event_surface import router as event_router
from .lifecycle import router as lifecycle_router
from .live_routes import router as live_router
from .markout_surface import router as markout_router
from .metrics_state import metrics
from .models import MarketSnapshot
from .observability import OperationLatencyTimer
from .replay import HistoricalReplay, ReplayFrame
from .research_persistence import persist_quant_lineage
from .safety_surface import router as safety_router
from .surface import router as surface_router
from .validation_surface import router as validation_router
from .websockets import hub

router = APIRouter(tags=["research"])
router.include_router(surface_router)
router.include_router(lifecycle_router)
router.include_router(safety_router)
router.include_router(circuit_router)
router.include_router(data_snooping_router)
router.include_router(event_router)
router.include_router(live_router)
router.include_router(markout_router)
router.include_router(validation_router)


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


class QuantReplayCalibrationObservation(BaseModel):
    observed_at: datetime
    probability: float = Field(ge=0.0, le=1.0)
    outcome: int = Field(ge=0, le=1)


class QuantReplayRequest(BaseModel):
    frames: list[QuantPipelineInput] = Field(min_length=1)
    calibration_observations: list[QuantReplayCalibrationObservation] = Field(
        default_factory=list
    )


def _validate_quant_replay_clock(request: QuantReplayRequest) -> None:
    frame_times = [frame.observed_at for frame in request.frames]
    if frame_times != sorted(frame_times):
        raise HTTPException(
            status_code=422,
            detail="quant replay frames must be ordered by observed_at",
        )
    for observation in request.calibration_observations:
        if observation.observed_at.tzinfo is None or observation.observed_at.utcoffset() is None:
            raise HTTPException(
                status_code=422,
                detail="calibration observation timestamps must be timezone-aware",
            )


def _past_calibration_samples(
    request: QuantReplayRequest,
    *,
    observed_at: datetime,
) -> tuple[CalibrationSample, ...]:
    return tuple(
        CalibrationSample(
            probability=observation.probability,
            outcome=observation.outcome,
        )
        for observation in request.calibration_observations
        if observation.observed_at < observed_at
    )


@router.post("/research/calibration")
def calibration(request: CalibrationRequest) -> dict[str, object]:
    with OperationLatencyTimer(metrics, "calibration"):
        report = calibration_report(
            [(item.probability, item.outcome) for item in request.observations],
            bin_count=request.bins,
        )
    metrics.increment("calibration_requests")
    return {
        "count": report.count,
        "brier_score": round(report.brier_score, 12),
        "log_loss": round(report.log_loss, 12),
        "expected_calibration_error": round(report.expected_calibration_error, 12),
        "maximum_calibration_error": round(report.maximum_calibration_error, 12),
        "reliability_curve": [
            {
                "lower_bound": bucket.lower,
                "upper_bound": bucket.upper,
                "count": bucket.count,
                "mean_prediction": bucket.mean_probability,
                "observed_frequency": bucket.observed_frequency,
                "absolute_gap": bucket.calibration_error,
            }
            for bucket in report.bins
            if bucket.count > 0
        ],
    }


@router.post("/research/quant/pipeline")
async def quant_pipeline(request: QuantPipelineInput) -> dict[str, object]:
    with OperationLatencyTimer(metrics, "quant_pipeline"):
        result = run_quant_pipeline(request)
        persisted = await persist_quant_lineage(persistence_engine, result)
    metrics.increment("quant_pipeline_requests")
    if persisted:
        metrics.increment("quant_pipeline_persisted")
    return {
        **result.model_dump(mode="json"),
        "persisted": persisted,
        "financial_connectivity": False,
        "real_money_execution": False,
    }


@router.post("/research/quant/replay")
async def quant_replay(request: QuantReplayRequest) -> dict[str, object]:
    _validate_quant_replay_clock(request)
    results: list[dict[str, object]] = []
    persisted_count = 0

    with OperationLatencyTimer(metrics, "quant_replay"):
        for frame in request.frames:
            replay_input = frame.model_copy(
                update={
                    "calibration_samples": _past_calibration_samples(
                        request,
                        observed_at=frame.observed_at,
                    )
                }
            )
            result = run_quant_pipeline(replay_input)
            persisted = await persist_quant_lineage(persistence_engine, result)
            if persisted:
                persisted_count += 1
            results.append(
                {
                    **result.model_dump(mode="json"),
                    "persisted": persisted,
                }
            )

    metrics.increment("quant_replay_requests")
    metrics.increment("quant_replay_frames", len(results))
    if persisted_count:
        metrics.increment("quant_replay_persisted", persisted_count)
    return {
        "mode": "HISTORICAL_REPLAY",
        "processed_frames": len(results),
        "persisted_frames": persisted_count,
        "frames": results,
        "calibration_policy": "STRICTLY_PAST_OBSERVATIONS_ONLY",
        "financial_connectivity": False,
        "real_money_execution": False,
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
    return {
        **metrics.snapshot(),
        "websocket": hub.snapshot(),
        "portfolio": portfolio.snapshot(),
    }


def _portfolio_gauges() -> dict[str, float]:
    snapshot = portfolio.snapshot()
    execution_costs = snapshot.get("execution_costs", {})
    slippage = (
        float(execution_costs.get("slippage", 0.0))
        if isinstance(execution_costs, dict)
        else 0.0
    )
    return {
        "portfolio_gross_exposure": float(snapshot.get("gross_exposure", 0.0)),
        "portfolio_net_exposure": float(snapshot.get("net_exposure", 0.0)),
        "portfolio_total_pnl_after_fees": float(snapshot.get("total_pnl_after_fees", 0.0)),
        "portfolio_realized_drawdown": float(snapshot.get("realized_drawdown", 0.0)),
        "portfolio_max_asset_concentration": float(
            snapshot.get("max_asset_concentration", 0.0)
        ),
        "portfolio_turnover_notional": float(snapshot.get("turnover_notional", 0.0)),
        "portfolio_slippage_cost": slippage,
        "portfolio_temporal_exposure_notional_seconds": float(
            snapshot.get("temporal_exposure_notional_seconds", 0.0)
        ),
        "portfolio_max_position_age_seconds": float(
            snapshot.get("max_position_age_seconds", 0.0)
        ),
    }


@router.get("/metrics/prometheus", response_class=PlainTextResponse)
def prometheus_metrics() -> str:
    snapshot = metrics.snapshot()
    websocket = hub.snapshot()
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
        "# HELP proto_ws_connections Current WebSocket connections.",
        "# TYPE proto_ws_connections gauge",
        f"proto_ws_connections {websocket['total_connections']}",
        "# HELP proto_ws_broadcasts_total WebSocket broadcast attempts.",
        "# TYPE proto_ws_broadcasts_total counter",
        f"proto_ws_broadcasts_total {websocket['broadcast_count']}",
        "# HELP proto_ws_send_failures_total WebSocket send failures.",
        "# TYPE proto_ws_send_failures_total counter",
        f"proto_ws_send_failures_total {websocket['send_failures']}",
        "# HELP proto_ws_origin_rejections_total WebSocket origin rejections.",
        "# TYPE proto_ws_origin_rejections_total counter",
        f"proto_ws_origin_rejections_total {websocket['origin_rejections']}",
        "# HELP proto_ws_capacity_rejections_total WebSocket capacity rejections.",
        "# TYPE proto_ws_capacity_rejections_total counter",
        f"proto_ws_capacity_rejections_total {websocket['capacity_rejections']}",
        "# HELP proto_ws_oversized_messages_total Oversized WebSocket frames.",
        "# TYPE proto_ws_oversized_messages_total gauge",
        f"proto_ws_oversized_messages_total {websocket['oversized_messages']}",
    ]
    for name, value in _portfolio_gauges().items():
        lines.extend(
            [
                f"# TYPE proto_{name} gauge",
                f"proto_{name} {value}",
            ]
        )
    for channel, value in websocket["connections"].items():
        lines.append(f'proto_ws_connections_by_channel{{channel="{channel}"}} {value}')
    for name, value in sorted(counters.items()):
        safe_name = "".join(character if character.isalnum() else "_" for character in name)
        lines.extend(
            [
                f"# TYPE proto_{safe_name}_total counter",
                f"proto_{safe_name}_total {value}",
            ]
        )
    for operation, latency in snapshot["operation_latency"].items():
        safe_operation = "".join(
            character if character.isalnum() else "_" for character in operation
        )
        lines.extend(
            [
                f"# TYPE proto_{safe_operation}_latency_ms gauge",
                f"proto_{safe_operation}_latency_ms {latency['average_ms']}",
                f"# TYPE proto_{safe_operation}_latency_samples_total counter",
                f"proto_{safe_operation}_latency_samples_total {latency['samples']}",
            ]
        )
    return "\n".join(lines) + "\n"


@router.post("/research/metrics/reset")
def reset_runtime_metrics() -> dict[str, object]:
    metrics.reset()
    return metrics.snapshot()
