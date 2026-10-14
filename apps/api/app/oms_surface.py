"""OMS, ledger and reconciliation surface (Phase 4). Paper execution only.

A paper venue stands in for BINANCE_SPOT: PAPER-stage proposals approved by
the Risk Engine are executed against live public quotes, with fills, fees and
reservations posted to the ledger. The paper account opens with the reference
capital in each quote currency (BRL and USDT; FX is not modelled). The ledger
is durable when persistence is enabled; the simulated venue is re-seeded from
it on start so the two stay reconciled across restarts. With persistence the
spent permits, the order book and the equity checkpoints are durable too, and
orders left open by a restart come back as UNKNOWN for the operator to resolve.

With OMS_EXECUTION_BACKEND=executor (Phase 5b) nothing executes in-process:
permitted intents go to the isolated executor (Binance testnet), and orders,
balances and reconciliation are read back from it. This process never holds
venue credentials.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime
from decimal import Decimal

from fastapi import APIRouter, HTTPException

from services.execution import RiskPermit, TradeIntent, VenueId
from services.execution.catalog import PAPER_INSTRUMENTS
from services.execution.constitution import REFERENCE_CAPITAL
from services.execution.venue import PaperVenueAdapter
from services.oms import (
    InMemoryCheckpoints,
    InMemoryLedger,
    OrderManager,
    OrderRejected,
    SqlCheckpoints,
    SqlLedger,
    SqlOrderStore,
    SqlPermitLedger,
    opening_balances,
    reconcile_balances,
)
from services.risk import AccountState, KillScope

from .app_state import persistence_engine
from .event_state import JournalUnavailable, record_operational_event
from .executor_client import ExecutorClient, ExecutorRefused, ExecutorUnavailable
from .market_quotes import live_quote
from .settings import settings

logger = logging.getLogger("proto.oms")

router = APIRouter(prefix="/v1/oms", tags=["oms"])

ACCOUNT_ID = "paper-binance-spot"
EXECUTOR_ACCOUNT_ID = "executor-binance-spot"
VENUE = VenueId.BINANCE_SPOT
_INSTRUMENTS = {k: v for k, v in PAPER_INSTRUMENTS.items() if v.venue is VENUE}
_QUOTE_ASSETS = sorted({i.quote_asset for i in _INSTRUMENTS.values()})
_BOUNDARY = {"financial_connectivity": False, "real_money_execution": False}

_oms: OrderManager | None = None
_ledger: InMemoryLedger | SqlLedger | None = None
_adapter: PaperVenueAdapter | None = None
_checkpoints: InMemoryCheckpoints | SqlCheckpoints | None = None
_executor: ExecutorClient | None = None
_executor_settings_id: int | None = None
_oms_settings_id: int | None = None
_start_lock = asyncio.Lock()


def _quote(instrument_id: str):
    return live_quote(instrument_id, market_source=settings.live_market_source)


async def _record(event_type: str, payload: dict) -> None:
    try:
        await record_operational_event(
            event_type=event_type, source="oms", payload={**payload, **_BOUNDARY}
        )
    except JournalUnavailable:
        logger.exception("%s was not journaled", event_type)


def executor_mode() -> bool:
    return settings.oms_execution_backend == "executor"


def executor_client() -> ExecutorClient:
    global _executor, _executor_settings_id
    if _executor is None or _executor_settings_id != id(settings):
        if not settings.executor_url or not settings.executor_api_token:
            raise ExecutorUnavailable("executor backend is not configured")
        _executor = ExecutorClient(settings.executor_url, settings.executor_api_token)
        _executor_settings_id = id(settings)
    return _executor


def _checkpoint_store() -> InMemoryCheckpoints | SqlCheckpoints:
    global _checkpoints
    if _checkpoints is None:
        account = EXECUTOR_ACCOUNT_ID if executor_mode() else ACCOUNT_ID
        _checkpoints = (
            SqlCheckpoints(persistence_engine, account_id=account)
            if persistence_engine is not None
            else InMemoryCheckpoints()
        )
    return _checkpoints


async def components() -> tuple[OrderManager, InMemoryLedger | SqlLedger, PaperVenueAdapter]:
    global _oms, _ledger, _adapter, _oms_settings_id
    async with _start_lock:
        if _oms is not None and _oms_settings_id == id(settings):
            assert _ledger is not None and _adapter is not None
            return _oms, _ledger, _adapter
        from .risk_surface import permit_ledger

        now = datetime.now(UTC)
        engine = persistence_engine
        if engine is not None:
            ledger: InMemoryLedger | SqlLedger = SqlLedger(
                engine, account_id=ACCOUNT_ID, venue=VENUE
            )
            permits = SqlPermitLedger(permit_ledger(), engine)
            store = SqlOrderStore(engine, account_id=ACCOUNT_ID)
        else:
            ledger = InMemoryLedger(account_id=ACCOUNT_ID, venue=VENUE)
            permits, store = permit_ledger(), None
        opening = {asset: REFERENCE_CAPITAL for asset in _QUOTE_ASSETS}
        await ledger.post(opening_balances(opening, account_id=ACCOUNT_ID, venue=VENUE, now=now))
        totals = {a: b.total for a, b in (await ledger.balances()).items()}
        adapter = PaperVenueAdapter(
            instruments=tuple(_INSTRUMENTS.values()),
            quotes=_quote,
            balances=totals,
            fee_bps=Decimal(str(settings.oms_paper_fee_bps)),
            slippage_bps=Decimal(str(settings.oms_paper_slippage_bps)),
            simulated_venue=VENUE,
        )
        oms = OrderManager(
            adapter=adapter,
            ledger=ledger,
            permits=permits,
            instruments=_INSTRUMENTS,
            recorder=_record,
            store=store,
        )
        recovered = await oms.recover()
        if recovered:
            logger.warning("recovered %d open orders as UNKNOWN or REJECTED", len(recovered))
        _oms, _ledger, _adapter = oms, ledger, adapter
        _oms_settings_id = id(settings)
        return oms, ledger, adapter


def reset_oms_state() -> None:
    global _oms, _ledger, _adapter, _checkpoints, _oms_settings_id
    global _executor, _executor_settings_id
    _oms = _ledger = _adapter = _checkpoints = _executor = None
    _oms_settings_id = _executor_settings_id = None


async def _totals() -> dict[str, Decimal]:
    """Ledger total per asset, from the in-process paper ledger or the executor."""

    if executor_mode():
        body = await executor_client().ledger()
        return {asset: Decimal(row["total"]) for asset, row in body["balances"].items()}
    _, ledger, _ = await components()
    return {asset: balance.total for asset, balance in (await ledger.balances()).items()}


async def account_state(quote_asset: str) -> AccountState:
    """Conservative account view in one quote currency, marked at live mid prices.

    A held asset without a live mark makes exposure unknown: gross exposure is
    then infinite (blocking new risk) and equity is held at its last baseline so
    a missing quote is never mistaken for a loss. Start-of-day and peak equity
    are checkpointed, durably when persistence is enabled. An unreachable
    executor blocks new risk the same way.
    """

    checkpoints = _checkpoint_store()
    try:
        balances = await _totals()
    except (ExecutorUnavailable, ExecutorRefused) as exc:
        logger.error("account state unavailable: %s", exc)
        saved = await checkpoints.get(quote_asset)
        baseline = saved[1] if saved else REFERENCE_CAPITAL
        return AccountState(
            equity=baseline,
            start_of_day_equity=baseline,
            peak_equity=max(saved[2], baseline) if saved else baseline,
            gross_exposure=Decimal("Infinity"),
            positions={},
        )
    cash = balances.get(quote_asset, Decimal("0"))
    positions: dict[str, Decimal] = {}
    exposure = Decimal("0")
    marked = True
    for instrument_id, instrument in _INSTRUMENTS.items():
        if instrument.quote_asset != quote_asset:
            continue
        quantity = balances.get(instrument.base_asset, Decimal("0"))
        positions[instrument_id] = quantity
        if quantity == 0:
            continue
        quote = _quote(instrument_id)
        if quote is None:
            marked = False
            continue
        exposure += quantity * (quote.bid + quote.ask) / 2

    today = datetime.now(UTC).date()
    saved = await checkpoints.get(quote_asset)
    day, start, peak = saved or (today, cash + exposure, cash + exposure)
    if not marked:
        return AccountState(
            equity=start,
            start_of_day_equity=start,
            peak_equity=max(peak, start),
            gross_exposure=Decimal("Infinity"),
            positions=positions,
        )
    equity = cash + exposure
    if day != today:
        day, start = today, equity
    checkpoint = (day, start, max(peak, equity))
    if checkpoint != saved:
        await checkpoints.put(quote_asset, checkpoint)
    return AccountState(
        equity=equity,
        start_of_day_equity=start,
        peak_equity=checkpoint[2],
        gross_exposure=exposure,
        positions=positions,
    )


def quote_asset_of(intent: TradeIntent) -> str:
    instrument = PAPER_INSTRUMENTS.get(intent.instrument_id)
    return instrument.quote_asset if instrument else "BRL"


async def _halt_venue(divergences: list[dict]) -> None:
    from .risk_surface import governor

    governor().trigger_kill(KillScope.VENUE, VENUE.value)
    await _record("RECONCILIATION_DIVERGENCE", {"venue": VENUE.value, "divergences": divergences})


async def reconcile() -> dict[str, object]:
    """Compare ledger totals with the venue; halt the venue on divergence."""

    if executor_mode():
        result = await executor_client().reconcile()
        divergences = list(result.get("divergences", []))
        if not result.get("consistent", False):
            await _halt_venue(divergences)
        return {
            "venue": VENUE.value,
            "consistent": bool(result.get("consistent")),
            "divergences": divergences,
            "venue_halted": not result.get("consistent", False),
            "execution_backend": "executor",
            **_BOUNDARY,
        }
    _, ledger, adapter = await components()
    found = reconcile_balances(await ledger.balances(), await adapter.balances())
    if found:
        await _halt_venue([d.to_dict() for d in found])
    return {
        "venue": VENUE.value,
        "consistent": not found,
        "divergences": [d.to_dict() for d in found],
        "venue_halted": bool(found),
        **_BOUNDARY,
    }


async def _dispatch(intent: TradeIntent, permit: RiskPermit) -> dict | None:
    """Hand a permitted intent to the isolated executor."""

    try:
        body = await executor_client().submit(intent, permit)
    except ExecutorRefused as exc:
        await _record(
            "ORDER_REFUSED",
            {"intent_id": str(intent.intent_id), "reason": exc.detail, "status": exc.status},
        )
        return None
    except ExecutorUnavailable as exc:
        # The executor may hold the order; dispatching this intent again is idempotent.
        await _record(
            "ORDER_DISPATCH_UNKNOWN", {"intent_id": str(intent.intent_id), "reason": str(exc)}
        )
        return None
    order = body["order"]
    await _record(f"ORDER_{order['status']}", order)
    if body.get("venue_halted"):
        await _halt_venue([])
    return order


async def execute(intent: TradeIntent, permit: RiskPermit) -> dict | None:
    """Execute an approved proposal: the isolated executor, or the in-process paper venue."""

    if executor_mode():
        return await _dispatch(intent, permit)
    if not settings.oms_paper_execution_enabled:
        return None
    oms, _, _ = await components()
    try:
        order = await oms.submit(intent, permit)
    except OrderRejected as exc:
        logger.warning("order refused: %s", exc.code)
        return None
    await reconcile()
    return order.to_dict()


async def _from_executor(call) -> dict:
    try:
        return await call
    except ExecutorRefused as exc:
        raise HTTPException(exc.status if exc.status < 500 else 502, exc.detail) from exc
    except ExecutorUnavailable as exc:
        raise HTTPException(503, "executor unavailable") from exc


@router.get("/orders")
async def list_orders(limit: int = 50) -> dict[str, object]:
    if executor_mode():
        body = await _from_executor(executor_client().orders(limit))
        return {"orders": body["orders"], "execution_backend": "executor", **_BOUNDARY}
    oms, _, _ = await components()
    orders = oms.orders[-max(1, min(limit, 500)) :]
    return {"orders": [o.to_dict() for o in reversed(orders)], **_BOUNDARY}


@router.get("/ledger")
async def ledger_view() -> dict[str, object]:
    if executor_mode():
        body = await _from_executor(executor_client().ledger())
        balances_view = body["balances"]
        account_id, mode = body.get("account_id", EXECUTOR_ACCOUNT_ID), "TESTNET"
    else:
        _, ledger, _ = await components()
        balances_view = {a: b.to_dict() for a, b in (await ledger.balances()).items()}
        account_id, mode = ACCOUNT_ID, "PAPER"
    accounts = {}
    for quote_asset in _QUOTE_ASSETS:
        state = await account_state(quote_asset)
        accounts[quote_asset] = {
            "equity": str(state.equity),
            "start_of_day_equity": str(state.start_of_day_equity),
            "peak_equity": str(state.peak_equity),
            "gross_exposure": str(state.gross_exposure),
        }
    return {
        "account_id": account_id,
        "venue": VENUE.value,
        "mode": mode,
        "balances": dict(balances_view),
        "accounts": accounts,
        **_BOUNDARY,
    }


@router.post("/reconcile")
async def reconcile_now() -> dict[str, object]:
    if executor_mode():
        return await _from_executor(reconcile())
    return await reconcile()


@router.post("/orders/{client_order_id}/resolve")
async def resolve_order(client_order_id: str) -> dict[str, object]:
    if executor_mode():
        body = await _from_executor(executor_client().order_action(client_order_id, "resolve"))
        return {"order": body["order"], **_BOUNDARY}
    oms, _, _ = await components()
    if oms.get(client_order_id) is None:
        raise HTTPException(404, "unknown order")
    order = await oms.resolve_unknown(client_order_id)
    return {"order": order.to_dict(), **_BOUNDARY}


@router.post("/orders/{client_order_id}/cancel")
async def cancel_order(client_order_id: str) -> dict[str, object]:
    if executor_mode():
        body = await _from_executor(executor_client().order_action(client_order_id, "cancel"))
        return {"order": body["order"], **_BOUNDARY}
    oms, _, _ = await components()
    if oms.get(client_order_id) is None:
        raise HTTPException(404, "unknown order")
    order = await oms.cancel(client_order_id)
    return {"order": order.to_dict(), **_BOUNDARY}
