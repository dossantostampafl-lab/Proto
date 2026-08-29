from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import APIRouter, HTTPException, Response

from services.market_data import live_readiness_failures

from .live_monitor import live_monitor
from .models import SystemMode
from .settings import settings

_NO_STORE_CACHE_CONTROL = "no-store, max-age=0"
_READINESS_RETRY_AFTER_SECONDS = "1"


def _mark_no_store(response: Response) -> None:
    response.headers["Cache-Control"] = _NO_STORE_CACHE_CONTROL


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
