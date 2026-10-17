"""OMS, ledger and reconciliation surface (Phase 4). Paper execution only.

A paper venue stands in for BINANCE_SPOT: PAPER-stage proposals approved by
the Risk Engine are executed against live public quotes, with fills, fees and
reservations posted to the ledger. The paper account opens with the reference
capital in each quote currency (BRL and USDT; FX is not modelled). The ledger
is durable on the control-plane store (control_state; on in the image, also in
LIVE_MONITORING); the simulated venue is re-seeded from
it on start so the two stay reconciled across restarts. With that store the
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

from . import control_state
from .event_state import JournalUnavailable, record_operational_event
from .executor_client import (
    SHORT_TIMEOUT,
    ExecutorClient,
    ExecutorRefused,
    ExecutorUnavailable,
)
from .instrument_registry import active_instruments
from .market_quotes import live_quote
from .settings import settings

logger = logging.getLogger("proto.oms")

router = APIRouter(prefix="/v1/oms", tags=["oms"])

ACCOUNT_ID = "paper-binance-spot"
EXECUTOR_ACCOUNT_ID = "executor-binance-spot"
XP_ACCOUNT_ID = "executor-xp-b3"
XP_VENUE = VenueId.XP_B3
VENUE = VenueId.BINANCE_SPOT
_INSTRUMENTS = {k: v for k, v in PAPER_INSTRUMENTS.items() if v.venue is VENUE}
_QUOTE_ASSETS = sorted({i.quote_asset for i in _INSTRUMENTS.values()})
_BOUNDARY = {"financial_connectivity": False, "real_money_execution": False}

_oms: OrderManager | None = None
_ledger: InMemoryLedger | SqlLedger | None = None
_adapter: PaperVenueAdapter | None = None
_checkpoints: dict[VenueId, InMemoryCheckpoints | SqlCheckpoints] = {}
_executors: dict[VenueId, ExecutorClient] = {}
_executor_settings_id: int | None = None
_oms_settings_id: int | None = None
# Opening holdings seen by the last good read of each venue's executor.
_last_openings: dict[VenueId, dict[str, Decimal]] = {}
_start_lock = asyncio.Lock()


def _control_engine():
    """The durable control-plane engine (also in production LIVE_MONITORING), or None."""

    return control_state.current().engine


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


def xp_enabled() -> bool:
    return settings.xp_routing_enabled


def executor_client(venue: VenueId = VENUE) -> ExecutorClient:
    """The isolated executor of one venue. Binance by default; XP_B3 only when configured."""

    global _executor_settings_id
    if _executor_settings_id != id(settings):
        _executors.clear()
        _executor_settings_id = id(settings)
    client = _executors.get(venue)
    if client is None:
        if venue is XP_VENUE:
            url, token = settings.xp_executor_url, settings.xp_executor_api_token
        else:
            url, token = settings.executor_url, settings.executor_api_token
        if not url or not token:
            raise ExecutorUnavailable("executor backend is not configured")
        client = _executors[venue] = ExecutorClient(
            url, token, timeout=SHORT_TIMEOUT if venue is XP_VENUE else None
        )
    return client


def _call_client(venue: VenueId) -> ExecutorClient:
    """Binance goes through ``executor_client()`` exactly as before; XP_B3 names its venue."""

    return executor_client() if venue is VENUE else executor_client(venue)


def _checkpoint_store(venue: VenueId = VENUE) -> InMemoryCheckpoints | SqlCheckpoints:
    store = _checkpoints.get(venue)
    if store is None:
        if venue is XP_VENUE:
            account = XP_ACCOUNT_ID
        else:
            account = EXECUTOR_ACCOUNT_ID if executor_mode() else ACCOUNT_ID
        engine = _control_engine()
        store = _checkpoints[venue] = (
            SqlCheckpoints(engine, account_id=account)
            if engine is not None
            else InMemoryCheckpoints()
        )
    return store


def venue_for_quote(quote_asset: str) -> VenueId:
    """BRL is XP's currency once XP routing is on; every other quote asset stays on Binance."""

    return XP_VENUE if xp_enabled() and quote_asset == "BRL" else VENUE


def _instruments_of(venue: VenueId):
    return {k: v for k, v in active_instruments().items() if v.venue is venue}


async def components() -> tuple[OrderManager, InMemoryLedger | SqlLedger, PaperVenueAdapter]:
    global _oms, _ledger, _adapter, _oms_settings_id
    async with _start_lock:
        if _oms is not None and _oms_settings_id == id(settings):
            assert _ledger is not None and _adapter is not None
            return _oms, _ledger, _adapter
        from .risk_surface import permit_ledger

        now = datetime.now(UTC)
        engine = _control_engine()
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
    global _oms, _ledger, _adapter, _oms_settings_id, _executor_settings_id
    _last_openings.clear()
    _checkpoints.clear()
    _executors.clear()
    _oms = _ledger = _adapter = None
    _oms_settings_id = _executor_settings_id = None


async def _totals(venue: VenueId = VENUE) -> tuple[dict[str, Decimal], dict[str, Decimal]]:
    """Ledger total and opening balance per asset, from the paper ledger or the executor.

    The opening balance is what the venue already held before PROTO traded (the testnet
    faucet's BTC, deposits). An executor that does not report it yields no opening, so the
    whole balance counts as PROTO's own: the stricter reading.
    """

    if executor_mode():
        body = await _call_client(venue).ledger()
        rows = body["balances"]
        return (
            {asset: Decimal(row["total"]) for asset, row in rows.items()},
            {asset: Decimal(row.get("opening", "0")) for asset, row in rows.items()},
        )
    if venue is not VENUE:
        raise ExecutorUnavailable(f"{venue.value} runs on its own executor only")
    _, ledger, _ = await components()
    balances = await ledger.balances()
    return (
        {asset: balance.total for asset, balance in balances.items()},
        {asset: balance.opening for asset, balance in balances.items()},
    )


def _book_key(quote_asset: str) -> str:
    """Checkpoint key for the own-book accounting basis (see ``_checkpoint_key``)."""

    return f"{quote_asset}:own"


def _checkpoint_key(
    quote_asset: str, openings: dict[str, Decimal], venue: VenueId = VENUE
) -> str:
    """Where this quote currency's start-of-day and peak equity are kept.

    Equity used to include the venue's opening holdings (the testnet faucet's 1 BTC), so values
    saved on that basis would read the switch to the own book as a ~89% loss and trip the
    daily-loss and drawdown lockdowns. Only a quote currency with such holdings moves to the
    ``<quote>:own`` key and starts from its own baseline once. Every other account keeps its
    saved checkpoint: its basis did not change, and a loss already on the books must stay there.
    """

    affected = any(
        openings.get(instrument.base_asset, Decimal("0")) > 0
        for instrument in _instruments_of(venue).values()
        if instrument.quote_asset == quote_asset
    )
    return _book_key(quote_asset) if affected else quote_asset


async def _outage_checkpoint(checkpoints, quote_asset: str, venue: VenueId = VENUE):
    """The checkpoint to hold equity at while the executor cannot be read.

    Same basis rule as a normal read, using the opening holdings seen last (they are a snapshot
    and never change). With none seen yet (a cold start during the outage) the basis is unknown,
    so take the more conservative of both keys: the lower start of day and the higher peak.
    """

    seen = _last_openings.get(venue)
    if seen is not None:
        return await checkpoints.get(_checkpoint_key(quote_asset, seen, venue))
    rows = [
        row
        for row in (
            await checkpoints.get(_book_key(quote_asset)),
            await checkpoints.get(quote_asset),
        )
        if row
    ]
    if not rows:
        return None
    return rows[0][0], min(row[1] for row in rows), max(row[2] for row in rows)


async def account_state(quote_asset: str) -> AccountState:
    """Conservative account view in one quote currency, marked at live mid prices.

    A held asset without a live mark makes exposure unknown: gross exposure is
    then infinite (blocking new risk) and equity is held at its last baseline so
    a missing quote is never mistaken for a loss. Start-of-day and peak equity
    are checkpointed, durably when persistence is enabled. An unreachable
    executor blocks new risk the same way.
    """

    venue = venue_for_quote(quote_asset)
    checkpoints = _checkpoint_store(venue)
    try:
        balances, openings = await _totals(venue)
    except (ExecutorUnavailable, ExecutorRefused) as exc:
        logger.error("account state unavailable: %s", exc)
        saved = await _outage_checkpoint(checkpoints, quote_asset, venue)
        baseline = saved[1] if saved else REFERENCE_CAPITAL
        return AccountState(
            equity=baseline,
            start_of_day_equity=baseline,
            peak_equity=max(saved[2], baseline) if saved else baseline,
            gross_exposure=Decimal("Infinity"),
            positions={},
        )
    _last_openings[venue] = openings
    cash = balances.get(quote_asset, Decimal("0"))
    positions: dict[str, Decimal] = {}
    exposure = Decimal("0")
    marked = True
    for instrument_id, instrument in _instruments_of(venue).items():
        if instrument.quote_asset != quote_asset:
            continue
        # PROTO's own position: what it bought, not what the venue already held. Opening
        # holdings are outside the book (they would swamp the 10k reference capital and swing
        # the daily loss with prices PROTO never traded).
        quantity = max(
            balances.get(instrument.base_asset, Decimal("0"))
            - openings.get(instrument.base_asset, Decimal("0")),
            Decimal("0"),
        )
        positions[instrument_id] = quantity
        if quantity == 0:
            continue
        quote = _quote(instrument_id)
        if quote is None:
            marked = False
            continue
        exposure += quantity * (quote.bid + quote.ask) / 2

    today = datetime.now(UTC).date()
    key = _checkpoint_key(quote_asset, openings, venue)
    saved = await checkpoints.get(key)
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
        await checkpoints.put(key, checkpoint)
    return AccountState(
        equity=equity,
        start_of_day_equity=start,
        peak_equity=checkpoint[2],
        gross_exposure=exposure,
        positions=positions,
    )


def quote_asset_of(intent: TradeIntent) -> str:
    instrument = active_instruments().get(intent.instrument_id)
    return instrument.quote_asset if instrument else "BRL"


async def _halt_venue(divergences: list[dict], venue: VenueId = VENUE) -> None:
    """Kill the diverged venue only: a Binance divergence never stops XP, nor the reverse."""

    from .risk_surface import governor_ready, persist_controls

    (await governor_ready()).trigger_kill(KillScope.VENUE, venue.value)
    if not await persist_controls():
        logger.critical("venue kill %s held in memory only", venue.value)
    await _record("RECONCILIATION_DIVERGENCE", {"venue": venue.value, "divergences": divergences})
    from .promotion_surface import violation

    await violation(venue=venue.value, kind="RECONCILIATION_DIVERGENCE", detail="ledger != venue")


async def reconcile(venue: VenueId = VENUE) -> dict[str, object]:
    """Compare ledger totals with the venue; halt that venue on divergence."""

    if executor_mode():
        result = await _call_client(venue).reconcile()
        divergences = list(result.get("divergences", []))
        if not result.get("consistent", False):
            await _halt_venue(divergences, venue)
        return {
            "venue": venue.value,
            "consistent": bool(result.get("consistent")),
            "divergences": divergences,
            "venue_halted": not result.get("consistent", False),
            "execution_backend": "executor",
            **_BOUNDARY,
        }
    if venue is not VENUE:
        raise ExecutorUnavailable(f"{venue.value} runs on its own executor only")
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
    """Hand a permitted intent to the isolated executor of its venue."""

    venue = XP_VENUE if intent.venue is XP_VENUE else VENUE
    if venue is XP_VENUE and not xp_enabled():
        await _record(
            "ORDER_REFUSED",
            {"intent_id": str(intent.intent_id), "reason": "xp_routing_not_configured"},
        )
        return None
    try:
        body = await _call_client(venue).submit(intent, permit)
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
        await _halt_venue([], venue)
    return order


async def execute(intent: TradeIntent, permit: RiskPermit) -> dict | None:
    """Execute an approved proposal: the isolated executor, or the in-process paper venue."""

    if executor_mode():
        return await _dispatch(intent, permit)
    if intent.venue is not VENUE:  # the in-process paper venue is Binance-only
        return None
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


def _venue_param(venue: str) -> VenueId:
    try:
        chosen = VenueId(venue.upper())
    except ValueError as exc:
        raise HTTPException(422, "unknown venue") from exc
    if chosen not in (VENUE, XP_VENUE):
        raise HTTPException(422, "venue must be BINANCE_SPOT or XP_B3")
    if chosen is XP_VENUE and not xp_enabled():
        raise HTTPException(409, "XP routing is not configured")
    return chosen


async def _from_executor(call) -> dict:
    try:
        return await call
    except ExecutorRefused as exc:
        raise HTTPException(exc.status if exc.status < 500 else 502, exc.detail) from exc
    except ExecutorUnavailable as exc:
        raise HTTPException(503, "executor unavailable") from exc


async def recent_orders(limit: int = 200) -> list[dict]:
    """Recent Binance orders as the API already reads them (executor or in-process paper OMS).

    Used to fold fills into the trader agents' books; raises ExecutorUnavailable/Refused when
    the executor cannot be read.
    """

    if executor_mode():
        body = await executor_client().orders(limit)
        return list(body.get("orders", []))
    oms, _, _ = await components()
    return [order.to_dict() for order in oms.orders[-max(1, min(limit, 500)) :]]


@router.get("/orders")
async def list_orders(limit: int = 50, venue: str = "BINANCE_SPOT") -> dict[str, object]:
    chosen = _venue_param(venue)
    if executor_mode():
        body = await _from_executor(_call_client(chosen).orders(limit))
        return {
            "orders": body["orders"],
            "venue": chosen.value,
            "execution_backend": "executor",
            **_BOUNDARY,
        }
    oms, _, _ = await components()
    orders = oms.orders[-max(1, min(limit, 500)) :]
    return {"orders": [o.to_dict() for o in reversed(orders)], **_BOUNDARY}


@router.get("/ledger")
async def ledger_view() -> dict[str, object]:
    """Balances and per-currency accounts. Every venue is asked at the same time, so a slow or
    switched-off one costs its own timeout once, not once per call, and never hides the other."""

    async def binance_part() -> tuple[dict, str, str, bool]:
        if not executor_mode():
            _, ledger, _ = await components()
            balances = {a: b.to_dict() for a, b in (await ledger.balances()).items()}
            return balances, ACCOUNT_ID, "PAPER", False
        try:
            body = await _from_executor(executor_client().ledger())
        except HTTPException:
            if not xp_enabled():  # unchanged without XP: one executor, one failure
                raise
            # With two venues one being down must not hide the other.
            return {}, EXECUTOR_ACCOUNT_ID, "TESTNET", True
        return (
            body["balances"],
            body.get("account_id", EXECUTOR_ACCOUNT_ID),
            "TESTNET",
            False,
        )

    async def xp_part() -> dict[str, object] | None:
        if not (executor_mode() and xp_enabled()):
            return None
        try:
            xp = await executor_client(XP_VENUE).ledger()
        except (ExecutorUnavailable, ExecutorRefused):
            return {"venue": XP_VENUE.value, "unavailable": True}
        return {
            "account_id": xp.get("account_id", XP_ACCOUNT_ID),
            "venue": XP_VENUE.value,
            "mode": "TESTNET",
            "balances": dict(xp["balances"]),
            "venue_halted": xp.get("venue_halted"),
        }

    async def account(quote_asset: str) -> dict[str, str]:
        state = await account_state(quote_asset)
        return {
            "equity": str(state.equity),
            "start_of_day_equity": str(state.start_of_day_equity),
            "peak_equity": str(state.peak_equity),
            "gross_exposure": str(state.gross_exposure),
        }

    results = await asyncio.gather(
        binance_part(), xp_part(), *(account(q) for q in _QUOTE_ASSETS), return_exceptions=True
    )
    for result in results:
        if isinstance(result, BaseException):
            raise result
    (balances_view, account_id, mode, binance_unavailable), xp_block = results[0], results[1]
    view: dict[str, object] = {
        "account_id": account_id,
        "venue": VENUE.value,
        "mode": mode,
        "balances": dict(balances_view),
        "accounts": dict(zip(_QUOTE_ASSETS, results[2:], strict=True)),
        **_BOUNDARY,
    }
    if binance_unavailable:
        view["unavailable"] = True
    if xp_block is not None:
        view["xp_b3"] = xp_block
    return view


@router.post("/reconcile")
async def reconcile_now() -> dict[str, object]:
    if executor_mode():
        if not xp_enabled():
            return await _from_executor(reconcile())
        # Each venue answers on its own: a Binance outage must not hide XP's reconciliation.
        results: dict[str, object] = {}
        for venue in (VENUE, XP_VENUE):
            try:
                results[venue.value] = await _from_executor(reconcile(venue))
            except HTTPException as exc:
                results[venue.value] = {
                    "venue": venue.value,
                    "consistent": False,
                    "unavailable": True,
                    "detail": exc.detail,
                    **_BOUNDARY,
                }
        return {**results[VENUE.value], "xp_b3": results[XP_VENUE.value]}
    return await reconcile()


@router.get("/executor/status")
async def executor_status(venue: str = "BINANCE_SPOT") -> dict[str, object]:
    """Read-only: whether the executor accepts orders (``ok``) or is ``halted``/``starting``."""

    chosen = _venue_param(venue)
    if not executor_mode():
        return {"venue": chosen.value, "status": "not_configured", **_BOUNDARY}
    try:
        body = await _call_client(chosen).health()
    except (ExecutorUnavailable, ExecutorRefused):
        return {"venue": chosen.value, "status": "unreachable", **_BOUNDARY}
    return {"venue": chosen.value, "status": str(body.get("status", "unknown")), **_BOUNDARY}


@router.post("/executor/halt/release")
async def release_executor_halt(venue: str = "BINANCE_SPOT") -> dict[str, object]:
    """Operator action: release the executor's halt (divergence or watchdog).

    The executor refuses while its ledger and the venue still disagree. The watchdog halts it
    again on its next cycle if the condition that tripped it still holds.
    """

    if not executor_mode():
        raise HTTPException(409, "no executor backend is configured")
    chosen = _venue_param(venue)
    body = await _from_executor(_call_client(chosen).release_halt())
    await _record(
        "EXECUTOR_HALT_RELEASED",
        {"by": "operator", "venue": chosen.value, "consistent": body.get("consistent")},
    )
    return {**body, **_BOUNDARY}


@router.post("/orders/{client_order_id}/resolve")
async def resolve_order(client_order_id: str, venue: str = "BINANCE_SPOT") -> dict[str, object]:
    chosen = _venue_param(venue)
    if executor_mode():
        body = await _from_executor(_call_client(chosen).order_action(client_order_id, "resolve"))
        return {"order": body["order"], **_BOUNDARY}
    oms, _, _ = await components()
    if oms.get(client_order_id) is None:
        raise HTTPException(404, "unknown order")
    order = await oms.resolve_unknown(client_order_id)
    return {"order": order.to_dict(), **_BOUNDARY}


@router.post("/orders/{client_order_id}/cancel")
async def cancel_order(client_order_id: str, venue: str = "BINANCE_SPOT") -> dict[str, object]:
    chosen = _venue_param(venue)
    if executor_mode():
        body = await _from_executor(_call_client(chosen).order_action(client_order_id, "cancel"))
        return {"order": body["order"], **_BOUNDARY}
    oms, _, _ = await components()
    if oms.get(client_order_id) is None:
        raise HTTPException(404, "unknown order")
    order = await oms.cancel(client_order_id)
    return {"order": order.to_dict(), **_BOUNDARY}
