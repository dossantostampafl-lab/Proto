from __future__ import annotations

from collections.abc import AsyncIterator, Mapping
from contextlib import asynccontextmanager

from fastapi import APIRouter, HTTPException, Response
from fastapi.responses import PlainTextResponse

from services.market_data import live_readiness_failures

from .live_monitor import live_monitor
from .models import SystemMode
from .settings import settings

_NO_STORE_CACHE_CONTROL = "no-store, max-age=0"
_READINESS_RETRY_AFTER_SECONDS = "1"


def _mark_no_store(response: Response) -> None:
    response.headers["Cache-Control"] = _NO_STORE_CACHE_CONTROL


def _metric_bool(value: object) -> int:
    return int(value is True)


def _prometheus_label(value: str) -> str:
    return value.replace("\\", "\\\\").replace("\n", "\\n").replace('"', '\\"')


def _append_optional_gauge(
    lines: list[str],
    *,
    metric: str,
    value: object,
    labels: str = "",
) -> None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return
    lines.append(f"{metric}{labels} {value}")


@asynccontextmanager
async def live_router_lifespan(_: APIRouter) -> AsyncIterator[None]:
    should_autostart = (
        settings.system_mode == SystemMode.LIVE_MONITORING.value
        and settings.live_monitoring_autostart
    )
    if should_autostart:
        await live_monitor.start()
    try:
        yield
    finally:
        if live_monitor.running:
            await live_monitor.stop()


router = APIRouter(
    prefix="/live",
    tags=["live-monitoring"],
    lifespan=live_router_lifespan,
)


@router.get("/status")
def live_status(response: Response) -> dict[str, object]:
    _mark_no_store(response)
    return live_monitor.status()


@router.get("/source-health")
def live_source_health(response: Response) -> dict[str, object]:
    _mark_no_store(response)
    return live_monitor.source_health()


@router.get("/ready")
def live_ready(response: Response) -> dict[str, object]:
    _mark_no_store(response)
    status = live_monitor.status()
    feed_health = status["feed_health"]
    connected = isinstance(feed_health, dict) and bool(feed_health.get("connected"))
    message_fresh = isinstance(feed_health, dict) and bool(feed_health.get("message_fresh"))
    failures = live_readiness_failures(
        running=bool(status["running"]),
        connected=connected,
        message_fresh=message_fresh,
        coverage=status,
    )
    ready = not failures
    if not ready:
        response.status_code = 503
        response.headers["Retry-After"] = _READINESS_RETRY_AFTER_SECONDS
    return {
        "status": "ready" if ready else "not_ready",
        "readiness_failures": failures,
        **status,
    }


@router.get("/market-data")
def live_market_data(response: Response) -> dict[str, object]:
    _mark_no_store(response)
    snapshots = live_monitor.snapshots()
    return {
        "mode": SystemMode.LIVE_MONITORING,
        "source": "PUBLIC_READ_ONLY",
        "count": len(snapshots),
        "markets": snapshots,
        "financial_connectivity": False,
        "real_money_execution": False,
    }


@router.get("/market-data/{symbol}")
def live_market_data_symbol(symbol: str, response: Response) -> dict[str, object]:
    _mark_no_store(response)
    snapshot = live_monitor.snapshot(symbol)
    if snapshot is None:
        raise HTTPException(
            status_code=404,
            detail="no live snapshot available for symbol",
            headers={"Cache-Control": _NO_STORE_CACHE_CONTROL},
        )
    return snapshot


@router.get("/analytics/{symbol}")
def live_analytics_symbol(symbol: str, response: Response) -> dict[str, object]:
    _mark_no_store(response)
    analytics = live_monitor.analytics(symbol)
    if analytics is None:
        raise HTTPException(
            status_code=404,
            detail="no live analytics available for symbol",
            headers={"Cache-Control": _NO_STORE_CACHE_CONTROL},
        )
    return analytics


@router.get("/metrics/prometheus", response_class=PlainTextResponse)
def live_prometheus_metrics(response: Response) -> str:
    _mark_no_store(response)
    status = live_monitor.status()
    feed_health = status.get("feed_health")
    health: Mapping[str, object] = feed_health if isinstance(feed_health, Mapping) else {}
    lines = [
        "# HELP proto_live_running Whether the read-only live monitor task is running.",
        "# TYPE proto_live_running gauge",
        f"proto_live_running {_metric_bool(status.get('running'))}",
        "# HELP proto_live_connected Whether the public market-data source is connected.",
        "# TYPE proto_live_connected gauge",
        f"proto_live_connected {_metric_bool(health.get('connected'))}",
        "# HELP proto_live_message_fresh Whether the public source has delivered a recent message.",
        "# TYPE proto_live_message_fresh gauge",
        f"proto_live_message_fresh {_metric_bool(health.get('message_fresh'))}",
        "# HELP proto_live_all_symbols_fresh Whether BTC ETH and SOL source timestamps are fresh.",
        "# TYPE proto_live_all_symbols_fresh gauge",
        f"proto_live_all_symbols_fresh {_metric_bool(status.get('all_symbols_fresh'))}",
        "# HELP proto_live_all_symbols_current_connection Whether all symbols are from the current socket generation.",
        "# TYPE proto_live_all_symbols_current_connection gauge",
        "proto_live_all_symbols_current_connection "
        f"{_metric_bool(status.get('all_symbols_current_connection'))}",
        "# HELP proto_live_financial_connectivity Financial account connectivity capability; invariant zero.",
        "# TYPE proto_live_financial_connectivity gauge",
        "proto_live_financial_connectivity 0",
        "# HELP proto_live_real_money_execution Real-money execution capability; invariant zero.",
        "# TYPE proto_live_real_money_execution gauge",
        "proto_live_real_money_execution 0",
    ]
    for metric, key in (
        ("proto_live_connection_generation", "connection_generation"),
        ("proto_live_connection_attempts_total", "connection_attempts"),
        ("proto_live_reconnects_total", "reconnect_count"),
        ("proto_live_frames_received_total", "frames_received"),
        ("proto_live_ticks_emitted_total", "ticks_emitted"),
        ("proto_live_parse_errors_total", "parse_error_count"),
        ("proto_live_message_timeouts_total", "message_timeout_count"),
        ("proto_live_consecutive_parse_errors", "consecutive_parse_errors"),
        ("proto_live_last_message_age_seconds", "last_message_age_seconds"),
        ("proto_live_last_tick_age_seconds", "last_tick_age_seconds"),
        ("proto_live_last_receipt_age_seconds", "last_receipt_age_seconds"),
    ):
        source = status if key == "last_receipt_age_seconds" else health
        _append_optional_gauge(lines, metric=metric, value=source.get(key))

    symbol_health = status.get("symbol_health")
    if isinstance(symbol_health, Mapping):
        for symbol in status.get("expected_symbols", []):
            if not isinstance(symbol, str):
                continue
            item = symbol_health.get(symbol)
            if not isinstance(item, Mapping):
                continue
            labels = f'{{symbol="{_prometheus_label(symbol)}"}}'
            lines.append(
                f"proto_live_symbol_fresh{labels} {_metric_bool(item.get('fresh'))}"
            )
            lines.append(
                "proto_live_symbol_current_connection"
                f"{labels} {_metric_bool(item.get('current_connection'))}"
            )
            _append_optional_gauge(
                lines,
                metric="proto_live_symbol_source_age_seconds",
                value=item.get("age_seconds"),
                labels=labels,
            )
            _append_optional_gauge(
                lines,
                metric="proto_live_symbol_receipt_age_seconds",
                value=item.get("receipt_age_seconds"),
                labels=labels,
            )
    return "\n".join(lines) + "\n"
