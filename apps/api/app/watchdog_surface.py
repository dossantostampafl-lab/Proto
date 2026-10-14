"""Independent watchdog surface (Phase 6b).

The watchdog process authenticates with its own token (`WATCHDOG_API_TOKEN`, header
`X-Proto-Watchdog-Token`). That token can do two things: report a heartbeat and trigger the
global kill switch. It cannot release anything: releasing a halt stays an operator action.

Heartbeats measure how long the watchdog has been watching without a gap. That coverage is what
the `WATCHDOG_ACTIVE` promotion evidence is recorded from.
"""

from __future__ import annotations

import hmac
import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from services.risk import KillScope

from .event_state import JournalUnavailable, record_operational_event
from .settings import settings

logger = logging.getLogger("proto.watchdog")

router = APIRouter(prefix="/v1/watchdog", tags=["watchdog"])
PREFIX = "/v1/watchdog/"

# A heartbeat later than this after the previous one breaks the coverage window.
MAX_GAP = timedelta(seconds=30)
# Coverage needed before WATCHDOG_ACTIVE evidence can pass.
MIN_COVERAGE = timedelta(hours=24)
_BOUNDARY = {"financial_connectivity": False, "real_money_execution": False}


def _utc_now() -> datetime:
    return datetime.now(UTC)


@dataclass
class WatchdogState:
    watchdog_id: str | None = None
    covered_since: datetime | None = None
    last_beat: datetime | None = None
    beats: int = 0
    last_report: dict[str, object] = field(default_factory=dict)
    trips: list[dict[str, object]] = field(default_factory=list)

    def beat(self, watchdog_id: str, report: dict[str, object], now: datetime) -> None:
        broken = (
            self.last_beat is None
            or now - self.last_beat > MAX_GAP
            or watchdog_id != self.watchdog_id
        )
        if broken:
            self.covered_since = now
            self.beats = 0
        self.watchdog_id = watchdog_id
        self.last_beat = now
        self.beats += 1
        self.last_report = report

    def active(self, now: datetime) -> bool:
        return self.last_beat is not None and now - self.last_beat <= MAX_GAP

    def coverage(self, now: datetime) -> timedelta:
        if not self.active(now) or self.covered_since is None:
            return timedelta(0)
        return now - self.covered_since

    def to_dict(self, now: datetime) -> dict[str, object]:
        return {
            "watchdog_id": self.watchdog_id,
            "active": self.active(now),
            "last_beat": self.last_beat.isoformat() if self.last_beat else None,
            "covered_since": self.covered_since.isoformat() if self.covered_since else None,
            "coverage_hours": round(self.coverage(now).total_seconds() / 3600, 2),
            "required_hours": MIN_COVERAGE.total_seconds() / 3600,
            "beats": self.beats,
            "last_report": self.last_report,
            "recent_trips": self.trips[-20:],
        }


_state = WatchdogState()


def state() -> WatchdogState:
    return _state


def reset_watchdog_state() -> None:
    global _state
    _state = WatchdogState()


def _authorize(token: str | None) -> None:
    expected = settings.watchdog_api_token
    if expected is None:
        raise HTTPException(503, "watchdog token is not configured")
    if token is None or not hmac.compare_digest(token.encode(), expected.encode()):
        raise HTTPException(401, "invalid watchdog token")


class HeartbeatBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    watchdog_id: str = Field(min_length=1, max_length=40)
    trips: list[str] = Field(default_factory=list, max_length=50)
    checks: dict[str, str] = Field(default_factory=dict, max_length=100)
    api_halted: bool = False
    executor_halted: bool = False


class HaltBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    watchdog_id: str = Field(min_length=1, max_length=40)
    reasons: list[str] = Field(min_length=1, max_length=50)


async def _journal(event_type: str, payload: dict[str, object]) -> None:
    try:
        await record_operational_event(event_type=event_type, source="watchdog", payload=payload)
    except JournalUnavailable:
        logger.exception("%s was not journaled", event_type)


@router.post("/heartbeat")
async def heartbeat(
    body: HeartbeatBody, x_proto_watchdog_token: str | None = Header(default=None)
) -> dict[str, object]:
    _authorize(x_proto_watchdog_token)
    now = _utc_now()
    _state.beat(body.watchdog_id, body.model_dump(), now)
    return {"accepted": True, **_BOUNDARY}


@router.post("/halt")
async def halt(
    body: HaltBody, x_proto_watchdog_token: str | None = Header(default=None)
) -> dict[str, object]:
    """Trigger the global kill switch. Idempotent; there is no watchdog release."""

    from .risk_surface import governor

    _authorize(x_proto_watchdog_token)
    gov = governor()
    already = bool(gov.snapshot()["global_kill"])
    gov.trigger_kill(KillScope.GLOBAL)
    now = _utc_now()
    if not already:
        _state.trips.append({"at": now.isoformat(), "reasons": body.reasons})
        await _journal(
            "WATCHDOG_TRIPPED", {"watchdog_id": body.watchdog_id, "reasons": body.reasons}
        )
    return {"controls": gov.snapshot(), "already_halted": already, **_BOUNDARY}


@router.get("/status")
def watchdog_status() -> dict[str, object]:
    return {**_state.to_dict(_utc_now()), **_BOUNDARY}
