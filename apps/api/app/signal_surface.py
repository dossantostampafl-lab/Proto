"""Signal ingestion surface. Signals carry opinions, never order authority."""

from __future__ import annotations

import logging
from collections import deque
from datetime import timedelta

from fastapi import APIRouter, HTTPException, Request, status

from services.execution import Signal
from services.signals.tradingview import (
    MAX_BODY_BYTES,
    TRADINGVIEW_WEBHOOK_IPS,
    IngestOutcome,
    SignalRejected,
    TradingViewIngestor,
)

from .event_state import record_operational_event
from .security import SlidingWindowRateLimiter, request_client_key
from .settings import settings

logger = logging.getLogger("proto.signals")

router = APIRouter(prefix="/v1/signals", tags=["signals"])

TRADINGVIEW_WEBHOOK_PATH = "/v1/signals/tradingview"
_RATE_LIMIT_PER_MINUTE = 120
_RECENT_CAPACITY = 200
_BOUNDARY = {
    "order_authority": False,
    "financial_connectivity": False,
    "real_money_execution": False,
}

_ingestor: TradingViewIngestor | None = None
_ingestor_settings_id: int | None = None
_limiter = SlidingWindowRateLimiter(limit=_RATE_LIMIT_PER_MINUTE)
_recent: deque[Signal] = deque(maxlen=_RECENT_CAPACITY)


def reset_signal_state() -> None:
    """Drop cached ingestor, rate-limit window and recent signals (tests, reconfiguration)."""

    global _ingestor, _ingestor_settings_id, _limiter
    _ingestor = None
    _ingestor_settings_id = None
    _limiter = SlidingWindowRateLimiter(limit=_RATE_LIMIT_PER_MINUTE)
    _recent.clear()


def _tradingview() -> TradingViewIngestor:
    global _ingestor, _ingestor_settings_id
    if _ingestor is not None and _ingestor_settings_id == id(settings):
        return _ingestor
    secret = settings.tradingview_webhook_secret
    if secret is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"code": "not_configured", "message": "TradingView webhook is not configured"},
        )
    _ingestor = TradingViewIngestor(
        secret=secret,
        allowed_instruments=settings.tradingview_instrument_allowlist,
        allowed_ips=TRADINGVIEW_WEBHOOK_IPS if settings.tradingview_ip_allowlist_enabled else None,
        max_age=timedelta(seconds=settings.tradingview_max_alert_age_seconds),
    )
    _ingestor_settings_id = id(settings)
    return _ingestor


def _client_ip(request: Request) -> str:
    if settings.tradingview_trust_forwarded_for:
        forwarded = request.headers.get("X-Forwarded-For", "")
        hops = [hop.strip() for hop in forwarded.split(",") if hop.strip()]
        if hops:
            # Our reverse proxy appends the address it saw; anything left of it is
            # client-controlled and must not be trusted.
            return hops[-1]
    return request_client_key(request)


@router.post("/tradingview")
async def tradingview_webhook(request: Request) -> dict[str, object]:
    ingestor = _tradingview()
    client_ip = _client_ip(request)
    if not _limiter.allow(client_ip):
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail={"code": "rate_limited", "message": "too many alerts"},
        )
    declared = request.headers.get("Content-Length")
    if declared is not None and declared.isdigit() and int(declared) > MAX_BODY_BYTES:
        raise HTTPException(
            status_code=status.HTTP_413_CONTENT_TOO_LARGE,
            detail={"code": "payload_too_large", "message": "alert body is too large"},
        )
    try:
        result = ingestor.ingest(await request.body(), client_ip)
    except SignalRejected as rejection:
        logger.warning("tradingview alert rejected: %s from %s", rejection.code, client_ip)
        raise HTTPException(
            status_code=rejection.status_code,
            detail={"code": rejection.code, "message": rejection.detail},
        ) from rejection

    outcome = result.outcome
    if outcome is IngestOutcome.ACCEPTED:
        try:
            await record_operational_event(
                event_type="TRADINGVIEW_SIGNAL_ACCEPTED",
                source="tradingview-webhook",
                payload={"signal": result.signal.model_dump(mode="json"), **_BOUNDARY},
                idempotency_key=f"tradingview:{result.signal.signal_id}",
            )
            _recent.append(result.signal)
        except ValueError:
            # Already journaled earlier (the in-memory dedupe window evicted it).
            outcome = IngestOutcome.DUPLICATE
    return {
        "outcome": outcome.value,
        "signal_id": str(result.signal.signal_id),
        **_BOUNDARY,
    }


@router.get("/recent")
def recent_signals() -> dict[str, object]:
    return {
        "signals": [signal.model_dump(mode="json") for signal in reversed(_recent)],
        **_BOUNDARY,
    }
