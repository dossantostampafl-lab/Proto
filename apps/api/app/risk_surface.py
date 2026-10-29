"""Risk Engine surface (Phase 3).

Every agent proposal is assessed here; approved proposals receive a signed,
single-use, short-lived permit. The Phase 4 OMS consumes it when paper
execution is enabled; otherwise it expires unused. Kill switches and lockdown
releases are operator actions (the operator-token middleware guards these POST
routes). Account state comes from the paper ledger, marked at live prices.
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

from services.execution import ReleaseStage, TradeIntent
from services.execution.constitution import (
    MAX_DAILY_LOSS,
    MAX_DRAWDOWN,
    MAX_GROSS_LEVERAGE,
    MAX_RISK_PER_POSITION,
    REFERENCE_CAPITAL,
)
from services.risk import (
    AccountState,
    Ed25519PermitSigner,
    GovernorError,
    KillScope,
    PermitLedger,
    PermitSigner,
    RiskDecision,
    RiskEngine,
    RiskGovernor,
    RiskPolicy,
)
from services.risk.permit_keys import decode_key

from .app_state import runtime
from .event_state import JournalUnavailable, record_operational_event
from .instrument_registry import active_instruments
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
        ttl = timedelta(seconds=settings.risk_permit_ttl_seconds)
        signer: PermitSigner | Ed25519PermitSigner
        if settings.risk_permit_private_key:
            # Asymmetric: the executor holds only the public key and cannot mint permits.
            signer = Ed25519PermitSigner(decode_key(settings.risk_permit_private_key), ttl=ttl)
        else:
            configured = settings.risk_permit_signing_key
            key = configured.encode() if configured else os.urandom(32)
            signer = PermitSigner(key, ttl=ttl)
        engine = RiskEngine(
            instruments=active_instruments(),
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
    global _governor, _ledger, _governor_settings_id, _controls_loaded, _controls_saved
    _governor = _ledger = None
    _governor_settings_id = None
    _controls_loaded = _controls_saved = None
    _decisions.clear()


# Kill switches and the lockdown latch are durable on the control-plane store (control_state).
CONTROLS_KEY = "risk_controls"
# (id(governor), store generation) the controls were last restored for.
_controls_loaded: tuple[int, int] | None = None
_controls_saved: dict[str, object] | None = None


async def governor_ready() -> RiskGovernor:
    """The governor with its durable controls restored (stricter merge), when a store exists.

    Nothing is ever written before the saved state is read, so an empty in-memory governor can
    never overwrite a persisted lockdown or kill switch.
    """

    global _controls_loaded, _controls_saved
    from services.risk import SqlControlState

    from . import control_state

    gov = governor()
    store = control_state.current()
    if store.engine is None or not await store.ensure():
        return gov
    marker = (id(gov), store.generation)
    if _controls_loaded == marker:
        return gov
    try:
        saved = await SqlControlState(store.engine).get(CONTROLS_KEY)
    except Exception as exc:
        store.mark_unavailable(exc)
        return gov
    if saved:
        gov.restore(saved)
    _controls_loaded, _controls_saved = marker, saved
    if gov.snapshot() != saved:
        await persist_controls()  # memory held something stricter (or nothing was saved yet)
    return gov


async def restore_controls() -> None:
    """Startup: restore kill switches and lockdown before any proposal is assessed."""

    await governor_ready()


async def persist_controls() -> bool:
    """Save the governor's controls. True when saved or when no durable store is configured."""

    global _controls_saved
    from services.risk import SqlControlState

    from . import control_state

    store = control_state.current()
    if store.engine is None:
        return True
    gov = await governor_ready()
    if _controls_loaded != (id(gov), store.generation):
        return False  # the saved state could not be read: never overwrite it blind
    snapshot = gov.snapshot()
    if snapshot == _controls_saved:
        return True
    try:
        await SqlControlState(store.engine).put(CONTROLS_KEY, snapshot)
    except Exception as exc:
        store.mark_unavailable(exc)
        return False
    _controls_saved = snapshot
    return True


def controls_durability() -> dict[str, object]:
    from . import control_state

    store = control_state.current()
    return {
        "durable": store.durable,
        "store": store.status.value,
        "accepts_new_risk": store.accepts_new_risk(),
    }


def _refused_unavailable() -> RiskDecision:
    """Fail closed: no permit while the durable control-plane state cannot be read or written."""

    detail = "fail: durable control store unreachable"
    return RiskDecision(
        approved=False,
        permit=None,
        rejections=("control_state_available",),
        checks={"control_state_available": detail},
        breach=None,
    )


async def account_state(quote_asset: str) -> AccountState:
    """Account view from the Phase 4 ledger, marked at live prices."""

    from .oms_surface import account_state as ledger_account_state

    return await ledger_account_state(quote_asset)


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

    from . import control_state
    from .fx_rates import canary_capital
    from .oms_surface import quote_asset_of
    from .promotion_surface import stage_for

    gov = await governor_ready()
    if not control_state.current().accepts_new_risk():
        decision = _refused_unavailable()
        record = {
            "intent_id": str(intent.intent_id),
            "intent_fingerprint": intent.fingerprint(),
            "instrument_id": intent.instrument_id,
            "strategy_id": intent.strategy_id,
            **decision.to_dict(),
            **_BOUNDARY,
        }
        _decisions.append(record)
        await _journal("RISK_DECISION", record)
        return decision
    quote_asset = quote_asset_of(intent)
    decision = gov.assess(
        intent,
        account=await account_state(quote_asset),
        quote=live_quote(intent.instrument_id, market_source=settings.live_market_source),
        now=datetime.now(UTC),
        external_global_kill=_external_global_kill(),
        scope_stage=await stage_for(intent),
        # Only a CANARY intent needs the BRL rate; nothing is fetched for paper.
        canary_capital=(
            await canary_capital(quote_asset, market_source=settings.live_market_source)
            if intent.stage is ReleaseStage.CANARY
            else None
        ),
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
        if not await persist_controls():
            logger.critical("lockdown %s latched in memory only", decision.breach.value)
        await _journal("RISK_LOCKDOWN_LATCHED", {"reason": decision.breach.value, **_BOUNDARY})
        from .promotion_surface import violation

        await violation(
            venue=intent.venue.value, kind="RISK_LOCKDOWN_LATCHED", detail=decision.breach.value
        )
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
            "signing_key": (
                "ed25519"
                if settings.risk_permit_private_key
                else "configured" if settings.risk_permit_signing_key else "ephemeral"
            ),
        },
        "controls": {
            **gov.snapshot(),
            "runtime_kill_switch": runtime.kill_switch,
            "automatic_kill": "on" if settings.risk_automatic_kill else "disabled_by_operator",
        },
        "controls_persistence": controls_durability(),
        # Where the account state is read from, as /health labels it (it was always
        # "paper_ledger", even when the testnet executor's ledger was the source).
        "account_source": "TESTNET" if settings.oms_execution_backend == "executor" else "PAPER",
        "decisions": len(_decisions),
        **_BOUNDARY,
    }


@router.get("/decisions/recent")
def recent_risk_decisions(limit: int = 20) -> dict[str, object]:
    selected = list(_decisions)[-max(1, min(limit, _DECISION_CAPACITY)) :]
    return {"decisions": list(reversed(selected)), **_BOUNDARY}


async def _release(gov: RiskGovernor, release) -> None:
    """Apply an operator release only if it can be saved; otherwise put the controls back.

    A release that lived only in memory would silently come back stricter after a restart,
    which is safe but misleading; refusing it keeps the operator's view truthful.
    """

    before = gov.snapshot()
    try:
        release()
    except GovernorError:
        gov.replace(before)  # all or nothing: one account still in breach keeps the latch
        raise
    if not await persist_controls():
        gov.replace(before)
        raise HTTPException(503, "durable control store unavailable; release not applied")


@router.post("/killswitch")
async def change_kill_switch(request: KillSwitchRequest) -> dict[str, object]:
    gov = await governor_ready()
    try:
        if request.action == "TRIGGER":
            gov.trigger_kill(request.scope, request.key)
            if not await persist_controls():
                logger.critical("kill switch %s held in memory only", request.scope.value)
        else:
            await _release(gov, lambda: gov.release_kill(request.scope, request.key))
    except GovernorError as exc:
        raise HTTPException(422, str(exc)) from exc
    await _journal(
        "RISK_KILL_SWITCH_CHANGED",
        {"scope": request.scope.value, "key": request.key, "action": request.action},
    )
    return {"controls": gov.snapshot(), **controls_durability(), **_BOUNDARY}


@router.post("/lockdown/release")
async def release_lockdown() -> dict[str, object]:
    from .oms_surface import _QUOTE_ASSETS

    gov = await governor_ready()
    accounts = [await account_state(asset) for asset in _QUOTE_ASSETS]

    def release_all() -> None:
        for account in accounts:
            gov.release_lockdown(account)

    try:
        await _release(gov, release_all)
    except GovernorError as exc:
        raise HTTPException(409, str(exc)) from exc
    await _journal("RISK_LOCKDOWN_RELEASED", {"by": "operator"})
    return {"controls": gov.snapshot(), **controls_durability(), **_BOUNDARY}
