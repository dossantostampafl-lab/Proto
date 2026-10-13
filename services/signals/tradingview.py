"""TradingView webhook alerts -> canonical Signals.

An alert is an opinion, never an order: it only becomes a `Signal` that later
phases may fuse into a `TradeIntent`. TradingView cannot send custom headers,
so the shared secret travels in the JSON body and is checked in constant time
before any schema detail is revealed. Official webhook IPs are allowlisted.
"""

from __future__ import annotations

import hmac
import json
from collections import OrderedDict
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from threading import Lock
from typing import Annotated
from uuid import NAMESPACE_URL, uuid5

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, ValidationError

from services.execution import Signal, SignalDirection, SignalSource

# https://www.tradingview.com/support/solutions/43000529348 (webhook source addresses)
TRADINGVIEW_WEBHOOK_IPS = frozenset(
    {"52.89.214.238", "34.212.75.30", "54.218.53.128", "52.32.178.7"}
)
MAX_BODY_BYTES = 4096
MAX_CLOCK_SKEW = timedelta(seconds=5)
MIN_SECRET_LENGTH = 32
_SIGNAL_NAMESPACE = uuid5(NAMESPACE_URL, "proto:signals:tradingview")


def _aware(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("fired_at must carry a timezone")
    return value.astimezone(UTC)


class TradingViewAlert(BaseModel):
    """The JSON message configured in the TradingView alert (see docs/tradingview)."""

    model_config = ConfigDict(frozen=True, extra="forbid", allow_inf_nan=False)

    secret: str
    alert_id: str = Field(min_length=1, max_length=120)
    strategy_id: str = Field(min_length=1, max_length=120)
    instrument_id: str = Field(min_length=3, max_length=160)
    direction: SignalDirection
    strength: float = Field(default=1.0, ge=0.0, le=1.0)
    timeframe: str = Field(min_length=1, max_length=20)
    fired_at: Annotated[datetime, AfterValidator(_aware)]


class IngestOutcome(StrEnum):
    ACCEPTED = "ACCEPTED"
    DUPLICATE = "DUPLICATE"


@dataclass(frozen=True)
class IngestResult:
    outcome: IngestOutcome
    signal: Signal


class SignalRejected(Exception):
    def __init__(self, code: str, status_code: int, detail: str) -> None:
        super().__init__(detail)
        self.code = code
        self.status_code = status_code
        self.detail = detail


def _utc_now() -> datetime:
    return datetime.now(UTC)


class TradingViewIngestor:
    def __init__(
        self,
        *,
        secret: str,
        allowed_instruments: frozenset[str],
        allowed_ips: frozenset[str] | None = TRADINGVIEW_WEBHOOK_IPS,
        max_age: timedelta = timedelta(seconds=30),
        dedupe_capacity: int = 10_000,
        clock: Callable[[], datetime] = _utc_now,
    ) -> None:
        if len(secret) < MIN_SECRET_LENGTH:
            raise ValueError("TradingView webhook secret must be at least 32 characters")
        if max_age <= timedelta(0) or dedupe_capacity < 1:
            raise ValueError("max_age and dedupe_capacity must be positive")
        self._secret = secret.encode()
        self._instruments = allowed_instruments
        self._ips = allowed_ips
        self._max_age = max_age
        self._capacity = dedupe_capacity
        self._clock = clock
        self._seen: OrderedDict[tuple[str, str], Signal] = OrderedDict()
        self._lock = Lock()

    def ingest(self, raw_body: bytes, client_ip: str) -> IngestResult:
        if self._ips is not None and client_ip not in self._ips:
            raise SignalRejected("ip_not_allowed", 403, "source address is not allowlisted")
        if len(raw_body) > MAX_BODY_BYTES:
            raise SignalRejected("payload_too_large", 413, "alert body is too large")
        try:
            document = json.loads(raw_body)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise SignalRejected("invalid_json", 400, "alert body is not JSON") from exc
        if not isinstance(document, dict):
            raise SignalRejected("invalid_json", 400, "alert body must be a JSON object")

        supplied = document.get("secret")
        if not isinstance(supplied, str) or not hmac.compare_digest(
            supplied.encode(), self._secret
        ):
            raise SignalRejected("unauthorized", 401, "alert identity was not verified")

        try:
            alert = TradingViewAlert.model_validate(document)
        except ValidationError as exc:
            fields = sorted({str(error["loc"][0]) for error in exc.errors() if error["loc"]})
            raise SignalRejected(
                "invalid_alert", 422, f"invalid fields: {', '.join(fields)}"
            ) from exc

        now = self._clock()
        if alert.fired_at < now - self._max_age:
            raise SignalRejected("stale_alert", 422, "alert is older than the allowed age")
        if alert.fired_at > now + MAX_CLOCK_SKEW:
            raise SignalRejected("future_alert", 422, "alert timestamp is in the future")
        if alert.instrument_id not in self._instruments:
            raise SignalRejected("instrument_not_allowed", 422, "instrument is not allowlisted")

        key = (alert.strategy_id, alert.alert_id)
        with self._lock:
            existing = self._seen.get(key)
            if existing is not None:
                self._seen.move_to_end(key)
                return IngestResult(IngestOutcome.DUPLICATE, existing)
            signal = Signal(
                signal_id=uuid5(_SIGNAL_NAMESPACE, f"{alert.strategy_id}\x00{alert.alert_id}"),
                source=SignalSource.TRADINGVIEW,
                strategy_id=alert.strategy_id,
                instrument_id=alert.instrument_id,
                direction=alert.direction,
                strength=alert.strength,
                timeframe=alert.timeframe,
                observed_at=alert.fired_at,
                # Tolerated forward skew must not produce a signal received before it fired.
                received_at=max(now, alert.fired_at),
            )
            self._seen[key] = signal
            if len(self._seen) > self._capacity:
                self._seen.popitem(last=False)
        return IngestResult(IngestOutcome.ACCEPTED, signal)

    def forget(self, signal: Signal) -> None:
        """Undo dedupe for a signal that could not be recorded downstream."""

        with self._lock:
            for key, seen in list(self._seen.items()):
                if seen.signal_id == signal.signal_id:
                    del self._seen[key]
