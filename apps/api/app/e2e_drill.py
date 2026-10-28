"""Closed-loop drill on the real stack: signal -> agents -> Risk permit -> executor (testnet)
-> order at the venue -> exit -> ledger -> reconciliation.

The drill sends one strong synthetic LONG signal for BTC/USDT through the same pipeline as a
TradingView alert or the own strategy, so it needs the same permit: nothing is bypassed. The
executor runs on the Binance Spot testnet only, so the order spends no real money. An order
still resting at the venue after the wait is cancelled.

The drill is a round trip: what the entry bought is sold again with a reduce-only SELL that
needs its own Risk Engine permit, so repeated drills never pile up inventory. Before the entry,
inventory earlier drills left behind (the own position minus every agent book; the opening
faucet holdings are never part of the own position) is sold the same way. The report and a
digest of its steps are journaled as E2E_DRILL_COMPLETED and served at /v1/drills/e2e/latest.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager, suppress
from datetime import UTC, datetime
from decimal import ROUND_DOWN, Decimal, InvalidOperation
from typing import Any
from uuid import UUID, uuid4

from fastapi import APIRouter, HTTPException

from services.agents.fusion import MAX_EXIT_SLIPPAGE_BPS, Candidate, SizingPolicy, build_exit
from services.execution import (
    ReleaseStage,
    Signal,
    SignalDirection,
    SignalSource,
    TradableInstrument,
)
from services.execution.catalog import B3_INSTRUMENTS, PAPER_INSTRUMENTS
from services.risk import RiskDecision

from . import agent_surface, oms_surface, risk_surface, roster_surface
from .event_state import JournalUnavailable, record_operational_event
from .instrument_registry import active_instruments
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
_EXIT_POLICY = SizingPolicy(strategy_id=STRATEGY, model_version="e2e-drill-exit-v1")
# Exits price this far under the bid, inside the Risk Engine's 50 bps collar.
EXIT_SLIPPAGE_BPS = MAX_EXIT_SLIPPAGE_BPS

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


def _floor_to_lot(quantity: Decimal, instrument: TradableInstrument) -> Decimal:
    if quantity <= 0:
        return Decimal("0")
    lots = (quantity / instrument.lot_size).to_integral_value(rounding=ROUND_DOWN)
    return lots * instrument.lot_size


def _filled(order: dict[str, Any] | None) -> Decimal:
    if order is None:
        return Decimal("0")
    try:
        return max(Decimal(str(order.get("filled_quantity") or "0")), Decimal("0"))
    except InvalidOperation:
        return Decimal("0")


def _text(quantity: Decimal) -> str:
    return format(quantity.normalize(), "f")


async def _executor_order(client_order_id: str) -> dict[str, Any] | None:
    body = await oms_surface.executor_client().orders(limit=100)
    return next(
        (o for o in body.get("orders", []) if o.get("client_order_id") == client_order_id), None
    )


async def _settle(order: dict[str, Any], sleep) -> tuple[dict[str, Any], bool]:
    """Wait for a terminal status; cancel what still rests after the wait."""

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
    return current, cancelled


async def _own_position(instrument: TradableInstrument) -> Decimal | None:
    """PROTO's own position (venue balance minus the opening/faucet holdings). None: unknown."""

    state = await oms_surface.account_state(instrument.quote_asset)
    return state.positions.get(instrument.instrument_id)


def _asset_key(instrument: TradableInstrument) -> tuple[str, str]:
    # BTCUSDT and BTCBRL hold the same coin: books are compared per venue and base asset.
    return instrument.venue.value, instrument.base_asset


async def _agent_holdings() -> dict[tuple[str, str], Decimal] | None:
    """Sum of the agents' book positions per (venue, base asset), ACTIVE and ELIMINATED alike.

    None when attribution data is unavailable: no roster store, no agents, fills that could not
    be synced, or a held book on an unknown instrument. Callers then never guess which coins
    are the drill's.
    """

    try:
        book = roster_surface.roster()
        await book.load()
        # The drill's own venue: holdings elsewhere are never sold here.
        if not await roster_surface.sync_fills([active_instruments()[INSTRUMENT].venue]):
            return None
        agents = book.agents()
    except Exception as exc:  # an unreadable roster is "unknown", never "empty"
        logger.warning("agent books unavailable: %s", type(exc).__name__)
        return None
    if not agents:
        return None
    catalog = {**PAPER_INSTRUMENTS, **B3_INSTRUMENTS}
    held: dict[tuple[str, str], Decimal] = {}
    for agent in agents:
        quantity = max(agent.book.quantity, Decimal("0"))
        instrument = catalog.get(agent.slot.instrument_id)
        if instrument is None:
            if quantity > 0:
                return None
            continue
        key = _asset_key(instrument)
        held[key] = held.get(key, Decimal("0")) + quantity
    return held


def _sellable() -> list[TradableInstrument]:
    """One instrument per coin: the drill's venue and quote currency."""

    drill = active_instruments()[INSTRUMENT]
    return [
        i
        for i in active_instruments().values()
        if i.venue is drill.venue and i.quote_asset == drill.quote_asset
    ]


async def _sell(
    instrument: TradableInstrument,
    quantity: Decimal,
    *,
    held: Decimal,
    signal_ids: tuple[UUID, ...],
) -> tuple[Candidate, RiskDecision | None, dict[str, Any] | None]:
    """Reduce-only SELL of ``quantity`` through the normal chain: Risk Engine permit -> OMS."""

    candidate = build_exit(
        instrument=instrument,
        quote=live_quote(instrument.instrument_id, market_source=settings.live_market_source),
        quantity=quantity,
        position_quantity=held,
        now=datetime.now(UTC),
        signal_ids=signal_ids,
        policy=_EXIT_POLICY,
        stage=ReleaseStage.PAPER,
        # The testnet book trails the public reference quote; at the bare bid an IOC exit
        # often finds no buyer there (2026-10-19: 0.1071 BTC cleanup CANCELED, 0 filled).
        slippage_bps=EXIT_SLIPPAGE_BPS,
    )
    if candidate.intent is None:
        return candidate, None, None
    risk = await risk_surface.assess(candidate.intent)
    order = None
    if risk.approved and risk.permit is not None:
        order = await oms_surface.execute(candidate.intent, risk.permit)
    return candidate, risk, order


async def _unwind_orphans(sleep) -> dict[str, Any]:
    """Sell inventory earlier drills left behind: the own position minus every agent book.

    Opening/faucet holdings are never part of the own position. Without attribution data
    nothing is sold. Returns the step detail; ``ok`` is False only when an unwind was refused.
    """

    holdings = await _agent_holdings()
    if holdings is None:
        return {"ok": True, "skipped": "agent attribution unavailable", "unwound": []}
    unwound: list[dict[str, Any]] = []
    ok = True
    for instrument in _sellable():
        own = await _own_position(instrument)
        if own is None:
            continue
        agents = holdings.get(_asset_key(instrument), Decimal("0"))
        orphan = _floor_to_lot(own - agents, instrument)
        if orphan <= 0:
            continue
        candidate, risk, order = await _sell(instrument, orphan, held=own, signal_ids=(uuid4(),))
        if candidate.intent is None:
            continue  # below one lot or the minimum notional: the dust stays
        entry: dict[str, Any] = {"instrument_id": instrument.instrument_id, "orphan": str(orphan)}
        unwound.append(entry)
        if risk is None or not risk.approved or order is None:
            ok = False
            entry["rejections"] = list(risk.rejections) if risk else ["not assessed"]
            continue
        settled, _ = await _settle(order, sleep)
        sold = _filled(settled)
        entry.update(status=settled.get("status"), quantity=str(sold))
        if sold > 0:
            await record_operational_event(
                event_type="DRILL_INVENTORY_UNWOUND",
                source="e2e-drill",
                payload={
                    "instrument_id": instrument.instrument_id,
                    "quantity": str(sold),
                    "orphan_quantity": str(orphan),
                    "agent_quantity": str(agents),
                    "own_position": str(own),
                    "client_order_id": settled.get("client_order_id"),
                    **_BOUNDARY,
                },
                idempotency_key=f"e2e-drill-unwind:{candidate.intent.intent_id}",
            )
    return {"ok": ok, "unwound": unwound}


async def _round_trip(
    instrument: TradableInstrument,
    entry: dict[str, Any],
    own_before: Decimal | None,
    signal: Signal,
    step: Callable[..., bool],
    sleep,
) -> None:
    """Close what the entry bought with a reduce-only SELL through the permit chain.

    The exit is capped by the entry's fill, by the own position's growth over the entry (a fee
    taken in coins cannot be sold) and by the own position minus every agent book, so it never
    sells agents' coins nor the opening holdings. With neither inventory cap known it does not
    sell at all.
    """

    bought = _filled(entry)
    if bought == 0:
        step("flat", True, entry_filled="0", exit_filled="0", residual="0")
        return
    own = await _own_position(instrument)
    holdings = await _agent_holdings()
    caps = [bought]
    if own is not None and own_before is not None:
        caps.append(own - own_before)
    if own is not None and holdings is not None:
        caps.append(own - holdings.get(_asset_key(instrument), Decimal("0")))
    if own is None or len(caps) == 1:
        step("exit_permit", False, rejections=["drill inventory unknown"])
        step("exit_order", False, status=None)
        step("flat", False, entry_filled=str(bought), exit_filled="0", residual=str(bought))
        return
    target = max(min(caps), Decimal("0"))
    candidate, risk, order = await _sell(
        instrument, target, held=own, signal_ids=(signal.signal_id,)
    )
    planned = candidate.intent.quantity if candidate.intent is not None else Decimal("0")
    permitted = bool(risk is not None and risk.approved and risk.permit is not None)
    if risk is not None:
        rejections = list(risk.rejections)
    else:
        rejections = [candidate.abstain_reason or "not assessed"]
    step(
        "exit_permit",
        permitted,
        quantity=str(planned),
        reduce_only=True,
        rejections=rejections,
    )
    settled: dict[str, Any] | None = None
    cancelled = False
    if order is not None:
        settled, cancelled = await _settle(order, sleep)
    step(
        "exit_order",
        settled is not None and settled.get("status") in _REACHED_VENUE,
        status=settled.get("status") if settled else None,
        filled_quantity=settled.get("filled_quantity") if settled else None,
        cancelled_after_wait=cancelled,
        venue_order_id=settled.get("venue_order_id") if settled else None,
    )
    sold = _filled(settled)
    step(
        "flat",
        permitted and planned > 0 and sold == planned,
        entry_filled=str(bought),
        exit_filled=str(sold),
        residual=_text(bought - sold),
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
            cleanup = await _unwind_orphans(sleep)
            if not step("inventory_cleanup", **cleanup):
                raise _Stop  # never add inventory while the old one cannot be sold
            instrument = active_instruments()[INSTRUMENT]
            own_before = await _own_position(instrument)
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
            current, cancelled = await _settle(order, sleep)
            step(
                "venue_order",
                current.get("status") in _REACHED_VENUE,
                status=current.get("status"),
                filled_quantity=current.get("filled_quantity"),
                cancelled_after_wait=cancelled,
                venue_order_id=current.get("venue_order_id"),
            )
            await _round_trip(instrument, current, own_before, signal, step, sleep)
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
