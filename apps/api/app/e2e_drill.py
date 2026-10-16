"""Closed-loop drill on the real stack: signal -> agents -> Risk permit -> executor (testnet)
-> order at the venue -> ledger -> reconciliation.

The drill sends one strong synthetic LONG signal for BTC/USDT through the same pipeline as a
TradingView alert or the own strategy, so it needs the same permit: nothing is bypassed. The
executor runs on the Binance Spot testnet only, so the order spends no real money. An order
still resting at the venue after the wait is cancelled. The report and a digest of its steps
are journaled as E2E_DRILL_COMPLETED and served at /v1/drills/e2e/latest.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from fastapi import APIRouter, HTTPException

from services.execution import Signal, SignalDirection, SignalSource

from . import agent_surface, oms_surface
from .event_state import JournalUnavailable, record_operational_event
from .market_quotes import live_quote
from .settings import settings

logger = logging.getLogger("proto.drill")

INSTRUMENT = "BINANCE_SPOT:BTCUSDT"
STRATEGY = "e2e-drill"
_WAIT_FOR_FILL_SECONDS = 45.0
_WAIT_FOR_READY_SECONDS = 180.0
_TERMINAL = {"FILLED", "CANCELED", "REJECTED"}
_REACHED_VENUE = {"ACKNOWLEDGED", "PARTIALLY_FILLED", "FILLED", "CANCELED"}
_BOUNDARY = {"financial_connectivity": False, "real_money_execution": False}

_latest: dict[str, Any] | None = None
_lock = asyncio.Lock()
_task: asyncio.Task[None] | None = None


def reset_drill_state() -> None:
    global _latest
    _latest = None


def _digest(steps: list[dict[str, Any]]) -> str:
    canonical = json.dumps(
        [{"step": s["step"], "ok": s["ok"]} for s in steps], sort_keys=True, separators=(",", ":")
    )
    return hashlib.sha256(canonical.encode()).hexdigest()


async def _executor_order(client_order_id: str) -> dict[str, Any] | None:
    body = await oms_surface.executor_client().orders(limit=100)
    return next(
        (o for o in body.get("orders", []) if o.get("client_order_id") == client_order_id), None
    )


async def run_drill(*, sleep=asyncio.sleep) -> dict[str, Any]:
    """Run the drill once; every step is recorded whether it passes or not."""

    global _latest
    async with _lock:
        started = datetime.now(UTC)
        steps: list[dict[str, Any]] = []

        def step(name: str, ok: bool, **detail: Any) -> bool:
            steps.append({"step": name, "ok": ok, **detail})
            return ok

        order: dict[str, Any] | None = None
        try:
            if not step(
                "executor_backend",
                oms_surface.executor_mode(),
                backend=settings.oms_execution_backend,
            ):
                raise _Stop
            quote = live_quote(INSTRUMENT, market_source=settings.live_market_source)
            if not step(
                "live_quote",
                quote is not None,
                market_source=settings.live_market_source,
                mid=str((quote.bid + quote.ask) / 2) if quote else None,
            ):
                raise _Stop
            now = datetime.now(UTC)
            signal = Signal(
                signal_id=uuid4(),
                source=SignalSource.PROTO_STRATEGY,
                strategy_id=STRATEGY,
                instrument_id=INSTRUMENT,
                direction=SignalDirection.LONG,
                strength=1.0,
                timeframe="drill",
                observed_at=now,
                received_at=now,
            )
            await record_operational_event(
                event_type="E2E_DRILL_SIGNAL",
                source="e2e-drill",
                payload={"signal": signal.model_dump(mode="json"), **_BOUNDARY},
                idempotency_key=f"e2e-drill:{signal.signal_id}",
            )
            agent_surface.buffer_signal(signal)
            try:
                decision, risk, order = await agent_surface.evaluate(INSTRUMENT)
            finally:
                agent_surface.forget_signal(signal)
            if not step(
                "agent_decision",
                decision.proposal is not None,
                outcome=decision.outcome.value,
                reason=decision.abstain_reason,
            ):
                raise _Stop
            if not step(
                "risk_permit",
                bool(risk and risk.approved),
                rejections=list(risk.rejections) if risk else ["not assessed"],
            ):
                raise _Stop
            if not step(
                "dispatched", order is not None, status=order.get("status") if order else None
            ):
                raise _Stop
            assert order is not None
            client_order_id = order["client_order_id"]
            waited = 0.0
            current = order
            while current.get("status") not in _TERMINAL and waited < _WAIT_FOR_FILL_SECONDS:
                await sleep(3.0)
                waited += 3.0
                current = await _executor_order(client_order_id) or current
            cancelled = False
            if current.get("status") not in _TERMINAL:
                await oms_surface.executor_client().order_action(client_order_id, "cancel")
                cancelled = True
                current = await _executor_order(client_order_id) or current
            step(
                "venue_order",
                current.get("status") in _REACHED_VENUE,
                status=current.get("status"),
                filled_quantity=current.get("filled_quantity"),
                cancelled_after_wait=cancelled,
                venue_order_id=current.get("venue_order_id"),
            )
            recon = await oms_surface.reconcile()
            step(
                "reconciliation",
                bool(recon.get("consistent")),
                divergences=len(recon.get("divergences", [])),
            )
        except _Stop:
            pass
        except Exception as exc:  # report the failure; never leave the drill half-told
            logger.exception("e2e drill failed")
            step("error", False, error=type(exc).__name__)

        verdict = "PASSED" if steps and all(s["ok"] for s in steps) else "FAILED"
        report = {
            "verdict": verdict,
            "instrument_id": INSTRUMENT,
            "started_at": started.isoformat(),
            "finished_at": datetime.now(UTC).isoformat(),
            "venue_environment": "TESTNET",
            "steps": steps,
            "digest": _digest(steps),
            **_BOUNDARY,
        }
        _latest = report
        try:
            await record_operational_event(
                event_type="E2E_DRILL_COMPLETED", source="e2e-drill", payload=report
            )
        except JournalUnavailable:
            logger.exception("e2e drill report was not journaled")
        return report


class _Stop(Exception):
    """A step failed; the remaining steps cannot run."""


async def _run_when_ready() -> None:
    waited = 0.0
    while waited < _WAIT_FOR_READY_SECONDS:
        if live_quote(INSTRUMENT, market_source=settings.live_market_source) is not None:
            break
        await asyncio.sleep(5.0)
        waited += 5.0
    await run_drill()


@asynccontextmanager
async def drill_lifespan(_: APIRouter) -> AsyncIterator[None]:
    global _task
    if settings.e2e_drill_on_start and settings.oms_execution_backend == "executor":
        _task = asyncio.get_running_loop().create_task(_run_when_ready(), name="e2e-drill")
    try:
        yield
    finally:
        if _task is not None:
            _task.cancel()
            with suppress(asyncio.CancelledError):
                await _task
            _task = None


router = APIRouter(prefix="/v1/drills/e2e", tags=["drills"], lifespan=drill_lifespan)


@router.get("/latest")
def latest_drill() -> dict[str, Any]:
    if _latest is None:
        return {"verdict": "NOT_RUN", **_BOUNDARY}
    return _latest


@router.post("/run")
async def run_drill_now() -> dict[str, Any]:
    """Operator-triggered drill (the operator token is enforced for every POST)."""

    if not oms_surface.executor_mode():
        raise HTTPException(409, "the closed-loop drill needs the executor backend")
    return await run_drill()
