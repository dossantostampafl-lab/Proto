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
from typing import Any

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

    def to_document(self) -> dict[str, object]:
        return {
            "watchdog_id": self.watchdog_id,
            "covered_since": self.covered_since.isoformat() if self.covered_since else None,
            "last_beat": self.last_beat.isoformat() if self.last_beat else None,
            "beats": self.beats,
            "trips": self.trips[-50:],
        }

    @classmethod
    def from_document(cls, raw: dict[str, Any]) -> WatchdogState:
        def when(value: object) -> datetime | None:
            return datetime.fromisoformat(str(value)) if value else None

        return cls(
            watchdog_id=raw.get("watchdog_id"),
            covered_since=when(raw.get("covered_since")),
            last_beat=when(raw.get("last_beat")),
            beats=int(raw.get("beats") or 0),
            trips=list(raw.get("trips") or []),
        )


_state = WatchdogState()
# Coverage is durable on the control-plane store: a quick deploy (shorter than MAX_GAP) does not
# reset the 24 h window, and a longer one breaks it on the next beat exactly as a gap would.
WATCHDOG_KEY = "watchdog_coverage"
_restored_generation: int | None = None


def state() -> WatchdogState:
    return _state


def reset_watchdog_state() -> None:
    global _state, _restored_generation
    _state = WatchdogState()
    _restored_generation = None


async def restore_watchdog() -> None:
    """Load the saved coverage window once per store connection (startup or recovery)."""

    global _state, _restored_generation
    from services.risk import SqlControlState

    from . import control_state

    store = control_state.current()
    if store.engine is None or not await store.ensure():
        return
    if _restored_generation == store.generation:
        return
    try:
        saved = await SqlControlState(store.engine).get(WATCHDOG_KEY)
    except Exception as exc:
        store.mark_unavailable(exc)
        return
    if saved and _state.last_beat is None:
        _state = WatchdogState.from_document(saved)
    _restored_generation = store.generation


async def _persist() -> None:
    from services.risk import SqlControlState

    from . import control_state

    store = control_state.current()
    if store.engine is None or _restored_generation != store.generation:
        return
    try:
        await SqlControlState(store.engine).put(WATCHDOG_KEY, _state.to_document())
    except Exception as exc:
        store.mark_unavailable(exc)


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
    await restore_watchdog()
    now = _utc_now()
    _state.beat(body.watchdog_id, body.model_dump(), now)
    await _persist()
    return {"accepted": True, **_BOUNDARY}


@router.post("/halt")
async def halt(
    body: HaltBody, x_proto_watchdog_token: str | None = Header(default=None)
) -> dict[str, object]:
    """Trigger the global kill switch. Idempotent; there is no watchdog release."""

    from .risk_surface import governor_ready, persist_controls

    _authorize(x_proto_watchdog_token)
    gov = await governor_ready()
    if not settings.risk_automatic_kill:
        # The operator turned automatic kills off: the trip is kept and shown, nothing is killed.
        now = _utc_now()
        _state.trips.append({"at": now.isoformat(), "reasons": body.reasons, "ignored": True})
        await _persist()
        await _journal(
            "WATCHDOG_TRIP_IGNORED", {"watchdog_id": body.watchdog_id, "reasons": body.reasons}
        )
        return {"controls": gov.snapshot(), "ignored": "automatic kill disabled", **_BOUNDARY}
    already = bool(gov.snapshot()["global_kill"])
    gov.trigger_kill(KillScope.GLOBAL)
    if not await persist_controls():
        logger.critical("watchdog halt held in memory only")
    now = _utc_now()
    if not already:
        _state.trips.append({"at": now.isoformat(), "reasons": body.reasons})
        await _persist()
        await _journal(
            "WATCHDOG_TRIPPED", {"watchdog_id": body.watchdog_id, "reasons": body.reasons}
        )
    return {"controls": gov.snapshot(), "already_halted": already, **_BOUNDARY}


@router.get("/status")
def watchdog_status() -> dict[str, object]:
    from . import control_state

    durable = control_state.current().durable and _restored_generation is not None
    return {**_state.to_dict(_utc_now()), "coverage_durable": durable, **_BOUNDARY}
