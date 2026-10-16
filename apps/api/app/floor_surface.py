"""Trading floor (Phase 7 preview): what every agent is doing, in real time.

Each operational-journal event is turned into one or more *floor actions*,
one per station of the authority chain:

    TradingView -> Fusion -> Reviewer -> Risk Engine -> OMS -> Reconciliation

Actions are pushed on `/ws/floor` as they are journaled, and `/v1/floor/state`
returns the recent ones with the controls and accounts to bootstrap the view.
Actions carry headlines, ids and the decision's own justification (`why`, copied from the
journaled payload, never inferred): never permit signatures, prompts or credentials, and the
data mode (PAPER or TESTNET) is always stated.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable
from typing import Any

from fastapi import APIRouter, WebSocket

from .settings import settings
from .websockets import hub

logger = logging.getLogger("proto.floor")

router = APIRouter(tags=["floor"])

STATIONS = ("tradingview", "fusion", "reviewer", "risk", "oms", "reconciliation")
CHANNEL = "floor"
_BOUNDARY = {"financial_connectivity": False, "real_money_execution": False}
_ORDER_TONE = {
    "SUBMITTED": "working",
    "ACKNOWLEDGED": "working",
    "PARTIALLY_FILLED": "working",
    "FILLED": "ok",
    "CANCELED": "warn",
    "RECOVERED": "warn",
    "UNKNOWN": "alert",
    "REJECTED": "alert",
    "REFUSED": "alert",
    "DISPATCH_UNKNOWN": "alert",
}
_VERDICT_TONE = {"APPROVE": "ok", "REDUCE": "warn", "DEFER": "alert", "VETO": "alert"}


def _clip(text: object, limit: int = 140) -> str:
    value = " ".join(str(text).split())
    return value if len(value) <= limit else value[: limit - 1] + "…"


def _symbol(instrument_id: object) -> str | None:
    if not isinstance(instrument_id, str):
        return None
    return instrument_id.partition(":")[2] or instrument_id


_WHY_ITEMS = 8
_SIZING_LABELS = (
    ("conviction", "conviction"),
    ("risk_budget", "risk budget"),
    ("equity", "sizing equity"),
    ("leverage_headroom", "leverage headroom"),
    ("position_quantity", "position"),
)


def _why(*items: object) -> list[str]:
    """The decision's reasons, as journaled: empty values dropped, each clipped."""

    return [_clip(item, 400) for item in items if item not in (None, "", [])][:_WHY_ITEMS]


def _fusion_why(payload: dict[str, Any]) -> list[str]:
    fusion = payload.get("fusion") or {}
    sizing = payload.get("sizing") or {}
    candidate = payload.get("candidate") or {}
    return _why(
        fusion.get("reason"),
        f"outcome {payload['outcome']}" if payload.get("outcome") else None,
        f"abstained: {payload['abstain_reason']}" if payload.get("abstain_reason") else None,
        f"candidate {candidate.get('side', '?')} {candidate.get('quantity', '?')} "
        f"@ {candidate.get('limit_price', '?')}"
        if candidate
        else None,
        *(f"{label} {sizing[key]}" for key, label in _SIZING_LABELS if key in sizing),
    )


def _action(
    event: dict[str, Any],
    station: str,
    tone: str,
    headline: str,
    instrument: object = None,
    why: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "seq": event["sequence"],
        "at": event["timestamp"],
        "event_type": event["event_type"],
        "station": station,
        "tone": tone,
        "headline": _clip(headline),
        "symbol": _symbol(instrument),
        "why": why or [],
    }


def floor_actions(event: dict[str, Any]) -> list[dict[str, Any]]:
    """Journal event -> station actions. Unknown event types animate nothing."""

    kind = str(event.get("event_type", ""))
    payload = event.get("payload") or {}

    if kind == "TRADINGVIEW_SIGNAL_ACCEPTED":
        signal = payload.get("signal") or {}
        headline = f"{signal.get('direction', '?')} alert · strength {signal.get('strength', '?')}"
        return [_action(event, "tradingview", "ok", headline, signal.get("instrument_id"))]

    if kind == "PROTO_STRATEGY_SIGNAL":
        signal = payload.get("signal") or {}
        strength = signal.get("strength", "?")
        headline = f"{signal.get('direction', '?')} · own strategy · strength {strength}"
        return [_action(event, "tradingview", "ok", headline, signal.get("instrument_id"))]

    if kind == "E2E_DRILL_COMPLETED":
        verdict = payload.get("verdict", "?")
        tone = "ok" if verdict == "PASSED" else "alert"
        instrument = payload.get("instrument_id")
        return [_action(event, "reconciliation", tone, f"closed test {verdict}", instrument)]

    if kind == "AGENT_DECISION":
        instrument = payload.get("instrument_id")
        fusion = payload.get("fusion") or {}
        outcome = payload.get("outcome", "?")
        actions = [
            _action(
                event,
                "fusion",
                "ok" if fusion.get("direction") in ("LONG", "SHORT") else "warn",
                f"{fusion.get('direction', '?')} · score {fusion.get('score', '?')}"
                + (f" · {payload['abstain_reason']}" if payload.get("abstain_reason") else ""),
                instrument,
                _fusion_why(payload),
            )
        ]
        review = payload.get("review")
        if review:
            verdict = str(review.get("verdict", "?"))
            actions.append(
                _action(
                    event,
                    "reviewer",
                    _VERDICT_TONE.get(verdict, "warn"),
                    f"{verdict} ({review.get('reviewer', '?')}): {review.get('rationale', '')}",
                    instrument,
                    _why(
                        review.get("rationale"),
                        f"size factor {review['size_factor']}"
                        if review.get("size_factor") is not None
                        else None,
                        "the reviewer can only shrink or block, never enlarge",
                    ),
                )
            )
        elif outcome == "ABSTAINED":
            actions.append(_action(event, "reviewer", "warn", "nothing to review", instrument))
        return actions

    if kind == "RISK_DECISION":
        instrument = payload.get("instrument_id")
        if payload.get("approved"):
            return [_action(event, "risk", "ok", "permit issued", instrument)]
        rejections = [str(r) for r in payload.get("rejections") or []]
        reasons = ", ".join(rejections[:3]) or "rejected"
        return [
            _action(event, "risk", "alert", f"rejected: {reasons}", instrument, _why(*rejections))
        ]
    if kind == "RISK_LOCKDOWN_LATCHED":
        return [_action(event, "risk", "alert", f"lockdown: {payload.get('reason', '?')}")]
    if kind == "RISK_LOCKDOWN_RELEASED":
        return [_action(event, "risk", "ok", "lockdown released by operator")]
    if kind == "RISK_KILL_SWITCH_CHANGED":
        action = payload.get("action", "?")
        headline = f"kill switch {payload.get('scope', '?')} {action}".strip()
        return [_action(event, "risk", "alert" if action == "TRIGGER" else "ok", headline)]

    if kind == "WATCHDOG_TRIPPED":
        tripped = [str(r) for r in payload.get("reasons") or []]
        reasons = ", ".join(tripped[:3]) or "tripped"
        return [_action(event, "risk", "alert", f"watchdog halt: {reasons}", why=_why(*tripped))]

    if kind.startswith("ORDER_"):
        status = kind.removeprefix("ORDER_")
        headline = f"{payload.get('side', '')} {payload.get('quantity', '')} {status}".strip()
        if payload.get("reason"):
            headline += f" · {payload['reason']}"
        tone = _ORDER_TONE.get(status, "working")
        return [_action(event, "oms", tone, headline, payload.get("instrument_id"))]

    if kind == "RECONCILIATION_DIVERGENCE":
        assets = sorted({str(d.get("asset", "?")) for d in payload.get("divergences") or []})
        detail = ", ".join(assets) if assets else "executor reported a divergence"
        return [_action(event, "reconciliation", "alert", f"divergence: {detail}")]
    return []


def _from_journal(events: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    actions: list[dict[str, Any]] = []
    for event in events:
        actions.extend(floor_actions(event))
    return actions


async def publish(event: dict[str, Any]) -> None:
    """Push the actions of a freshly journaled event to every open floor view."""

    for action in floor_actions(event):
        try:
            await hub.broadcast(CHANNEL, {"type": "floor", "data": action})
        except Exception:  # a slow viewer must never fail the journal write
            logger.exception("floor broadcast failed")


def data_mode() -> str:
    return "TESTNET" if settings.oms_execution_backend == "executor" else "PAPER"


@router.get("/v1/floor/state")
async def floor_state(limit: int = 80) -> dict[str, object]:
    from .event_state import operational_journal
    from .oms_surface import _QUOTE_ASSETS, account_state
    from .risk_surface import governor, runtime
    from .watchdog_surface import _utc_now
    from .watchdog_surface import state as watchdog_state

    recent = operational_journal.journal.recent(500)
    actions = _from_journal(event.to_dict() for event in recent)[-max(1, min(limit, 300)) :]
    stations: dict[str, dict[str, Any] | None] = dict.fromkeys(STATIONS)
    for action in actions:
        stations[action["station"]] = action

    accounts: dict[str, object] = {}
    for asset in _QUOTE_ASSETS:
        try:
            state = await account_state(asset)
        except Exception:  # an unreadable account is shown as unavailable, never guessed
            logger.exception("floor: account %s unavailable", asset)
            accounts[asset] = None
            continue
        accounts[asset] = {
            "equity": str(state.equity),
            "start_of_day_equity": str(state.start_of_day_equity),
            "peak_equity": str(state.peak_equity),
            "gross_exposure": str(state.gross_exposure),
            "positions": {k: str(v) for k, v in state.positions.items() if v},
        }

    return {
        "promotion": await _promotion_summary(),
        "watchdog": _watchdog_summary(watchdog_state().to_dict(_utc_now())),
        "data_mode": data_mode(),
        "journal": operational_journal.integrity.value,
        "stations": stations,
        "actions": actions,
        "controls": {**governor().snapshot(), "runtime_kill_switch": runtime.kill_switch},
        "accounts": accounts,
        "pipeline": {
            "agents_enabled": settings.agent_pipeline_enabled,
            "reviewer": settings.agent_reviewer,
            "execution_backend": settings.oms_execution_backend,
            "paper_execution_enabled": settings.oms_paper_execution_enabled,
        },
        **_BOUNDARY,
    }


def _watchdog_summary(status: dict[str, Any]) -> dict[str, Any]:
    """What the floor shows of the watchdog: liveness, coverage and the last halt."""

    trips = status.get("recent_trips") or []
    return {
        "active": status["active"],
        "last_beat": status["last_beat"],
        "coverage_hours": status["coverage_hours"],
        "required_hours": status["required_hours"],
        "trips": len(trips),
        "last_trip": trips[-1] if trips else None,
    }


async def _promotion_summary() -> list[dict[str, Any]]:
    """Each scope's stage, clean days and what blocks its next step (operator aside)."""

    from datetime import UTC, datetime

    from services.promotion import gate_check, next_stage

    from .promotion_surface import registry

    now = datetime.now(UTC)
    summary = []
    try:
        records = await registry().records()
    except Exception:  # promotion data unavailable is shown as such, never guessed
        logger.exception("floor: promotion registry unavailable")
        return []
    for record in records:
        target = next_stage(record.stage)
        summary.append(
            {
                "scope": record.scope.to_dict(),
                "stage": record.stage.value,
                "clean_days": round(record.clean_for(now).total_seconds() / 86_400, 2),
                "next_stage": target.value if target else None,
                "blockers": gate_check(record, target, now=now, operator=True) if target else [],
                "violations": len(record.violations),
            }
        )
    return summary


@router.websocket("/ws/floor")
async def ws_floor(websocket: WebSocket) -> None:
    await hub.serve(CHANNEL, websocket)
