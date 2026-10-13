"""Risk Engine surface (Phase 3).

Every agent proposal is assessed here; approved proposals receive a signed,
single-use, short-lived permit. There is no OMS yet (Phase 4), so permits are
recorded and expire unused. Kill switches and lockdown releases are operator
actions (the operator-token middleware guards these POST routes). Until the
Phase 4 ledger exists the account is the reference capital with no positions.
"""

from __future__ import annotations

import logging
import os
from collections import deque
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from services.execution import TradeIntent
from services.execution.catalog import PAPER_INSTRUMENTS
from services.execution.constitution import (
    MAX_DAILY_LOSS,
    MAX_DRAWDOWN,
    MAX_GROSS_LEVERAGE,
    MAX_RISK_PER_POSITION,
    REFERENCE_CAPITAL,
)
from services.risk import (
    AccountState,
    GovernorError,
    KillScope,
    PermitLedger,
    PermitSigner,
    RiskDecision,
    RiskEngine,
    RiskGovernor,
    RiskPolicy,
)

from .app_state import runtime
from .event_state import JournalUnavailable, record_operational_event
from .market_quotes import live_quote
from .models import KillSwitchState
from .settings import settings

logger = logging.getLogger("proto.risk")

router = APIRouter(prefix="/v1/risk", tags=["risk"])

_DECISION_CAPACITY = 100
_BOUNDARY = {"financial_connectivity": False, "real_money_execution": False}

_governor: RiskGovernor | None = None
_ledger: PermitLedger | None = None
_governor_settings_id: int | None = None
_decisions: deque[dict[str, object]] = deque(maxlen=_DECISION_CAPACITY)


def _build() -> tuple[RiskGovernor, PermitLedger]:
    global _governor, _ledger, _governor_settings_id
    if _governor is None or _ledger is None or _governor_settings_id != id(settings):
        configured = settings.risk_permit_signing_key
        key = configured.encode() if configured else os.urandom(32)
        signer = PermitSigner(key, ttl=timedelta(seconds=settings.risk_permit_ttl_seconds))
        engine = RiskEngine(
            instruments=PAPER_INSTRUMENTS,
            signer=signer,
            policy=RiskPolicy(
                stop_distance=Decimal(str(settings.risk_stop_distance)),
                max_quote_age=timedelta(seconds=settings.risk_max_quote_age_seconds),
                price_collar_bps=Decimal(str(settings.risk_price_collar_bps)),
            ),
        )
        _governor, _ledger = RiskGovernor(engine), PermitLedger(signer)
        _governor_settings_id = id(settings)
    return _governor, _ledger


def governor() -> RiskGovernor:
    return _build()[0]


def permit_ledger() -> PermitLedger:
    """The single-use ledger the Phase 4 OMS must consume permits through."""

    return _build()[1]


def reset_risk_state() -> None:
    global _governor, _ledger, _governor_settings_id
    _governor = _ledger = None
    _governor_settings_id = None
    _decisions.clear()


def account_state() -> AccountState:
    return AccountState(
        equity=REFERENCE_CAPITAL,
        start_of_day_equity=REFERENCE_CAPITAL,
        peak_equity=REFERENCE_CAPITAL,
        gross_exposure=Decimal("0"),
        positions={},
    )


def _external_global_kill() -> bool:
    return runtime.kill_switch != KillSwitchState.ARMED


async def _journal(event_type: str, payload: dict[str, object], key: str | None = None) -> None:
    try:
        await record_operational_event(
            event_type=event_type, source="risk-engine", payload=payload, idempotency_key=key
        )
    except JournalUnavailable:
        logger.exception("%s was not journaled", event_type)


async def assess(intent: TradeIntent) -> RiskDecision:
    """Assess one proposal and journal the outcome (without the permit signature)."""

    decision = governor().assess(
        intent,
        account=account_state(),
        quote=live_quote(intent.instrument_id, market_source=settings.live_market_source),
        now=datetime.now(UTC),
        external_global_kill=_external_global_kill(),
    )
    record = {
        "intent_id": str(intent.intent_id),
        "intent_fingerprint": intent.fingerprint(),
        "instrument_id": intent.instrument_id,
        "strategy_id": intent.strategy_id,
        **decision.to_dict(),
        **_BOUNDARY,
    }
    _decisions.append(record)
    key = f"risk-decision:{decision.permit.permit_id}" if decision.permit else None
    await _journal("RISK_DECISION", record, key)
    if decision.breach is not None:
        await _journal("RISK_LOCKDOWN_LATCHED", {"reason": decision.breach.value, **_BOUNDARY})
    return decision


class KillSwitchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    scope: KillScope
    key: str | None = Field(default=None, min_length=1, max_length=120)
    action: Literal["TRIGGER", "RELEASE"]


@router.get("/status")
def risk_status() -> dict[str, object]:
    gov = governor()
    policy = gov.engine.policy
    return {
        "constitution": {
            "reference_capital": str(REFERENCE_CAPITAL),
            "max_risk_per_position": str(MAX_RISK_PER_POSITION),
            "max_daily_loss": str(MAX_DAILY_LOSS),
            "max_drawdown": str(MAX_DRAWDOWN),
            "max_gross_leverage": str(MAX_GROSS_LEVERAGE),
        },
        "policy": {
            "stop_distance": str(policy.stop_distance),
            "max_quote_age_seconds": policy.max_quote_age.total_seconds(),
            "price_collar_bps": str(policy.price_collar_bps),
            "permit_ttl_seconds": settings.risk_permit_ttl_seconds,
            "signing_key": "configured" if settings.risk_permit_signing_key else "ephemeral",
        },
        "controls": {**gov.snapshot(), "runtime_kill_switch": runtime.kill_switch},
        "account_source": "reference_capital_until_ledger",
        "decisions": len(_decisions),
        **_BOUNDARY,
    }


@router.get("/decisions/recent")
def recent_risk_decisions(limit: int = 20) -> dict[str, object]:
    selected = list(_decisions)[-max(1, min(limit, _DECISION_CAPACITY)) :]
    return {"decisions": list(reversed(selected)), **_BOUNDARY}


@router.post("/killswitch")
async def change_kill_switch(request: KillSwitchRequest) -> dict[str, object]:
    gov = governor()
    try:
        if request.action == "TRIGGER":
            gov.trigger_kill(request.scope, request.key)
        else:
            gov.release_kill(request.scope, request.key)
    except GovernorError as exc:
        raise HTTPException(422, str(exc)) from exc
    await _journal(
        "RISK_KILL_SWITCH_CHANGED",
        {"scope": request.scope.value, "key": request.key, "action": request.action},
    )
    return {"controls": gov.snapshot(), **_BOUNDARY}


@router.post("/lockdown/release")
async def release_lockdown() -> dict[str, object]:
    gov = governor()
    try:
        gov.release_lockdown(account_state())
    except GovernorError as exc:
        raise HTTPException(409, str(exc)) from exc
    await _journal("RISK_LOCKDOWN_RELEASED", {"by": "operator"})
    return {"controls": gov.snapshot(), **_BOUNDARY}
