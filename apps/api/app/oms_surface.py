"""OMS, ledger and reconciliation surface (Phase 4). Paper execution only.

A paper venue stands in for BINANCE_SPOT: PAPER-stage proposals approved by
the Risk Engine are executed against live public quotes, with fills, fees and
reservations posted to the ledger. The paper account opens with the reference
capital in each quote currency (BRL and USDT; FX is not modelled). The ledger
is durable when persistence is enabled; the simulated venue is re-seeded from
it on start so the two stay reconciled across restarts.
"""

from __future__ import annotations

import asyncio
import logging
from collections import defaultdict
from datetime import UTC, date, datetime
from decimal import Decimal

from fastapi import APIRouter, HTTPException

from services.execution import RiskPermit, TradeIntent, VenueId
from services.execution.catalog import PAPER_INSTRUMENTS
from services.execution.constitution import REFERENCE_CAPITAL
from services.execution.venue import PaperVenueAdapter
from services.oms import (
    InMemoryLedger,
    ManagedOrder,
    OrderManager,
    OrderRejected,
    SqlLedger,
    opening_balances,
    reconcile_balances,
)
from services.risk import AccountState, KillScope

from .app_state import persistence_engine
from .event_state import JournalUnavailable, record_operational_event
from .market_quotes import live_quote
from .settings import settings

logger = logging.getLogger("proto.oms")

router = APIRouter(prefix="/v1/oms", tags=["oms"])

ACCOUNT_ID = "paper-binance-spot"
VENUE = VenueId.BINANCE_SPOT
_INSTRUMENTS = {k: v for k, v in PAPER_INSTRUMENTS.items() if v.venue is VENUE}
_QUOTE_ASSETS = sorted({i.quote_asset for i in _INSTRUMENTS.values()})
_BOUNDARY = {"financial_connectivity": False, "real_money_execution": False}

_oms: OrderManager | None = None
_ledger: InMemoryLedger | SqlLedger | None = None
_adapter: PaperVenueAdapter | None = None
_oms_settings_id: int | None = None
_start_lock = asyncio.Lock()
# Per quote asset: (UTC day, start-of-day equity) and the running peak.
_start_of_day: dict[str, tuple[date, Decimal]] = {}
_peak: dict[str, Decimal] = {}


def _quote(instrument_id: str):
    return live_quote(instrument_id, market_source=settings.live_market_source)


async def _record(event_type: str, payload: dict) -> None:
    try:
        await record_operational_event(
            event_type=event_type, source="oms", payload={**payload, **_BOUNDARY}
        )
    except JournalUnavailable:
        logger.exception("%s was not journaled", event_type)


async def components() -> tuple[OrderManager, InMemoryLedger | SqlLedger, PaperVenueAdapter]:
    global _oms, _ledger, _adapter, _oms_settings_id
    async with _start_lock:
        if _oms is not None and _oms_settings_id == id(settings):
            assert _ledger is not None and _adapter is not None
            return _oms, _ledger, _adapter
        from .risk_surface import permit_ledger

        now = datetime.now(UTC)
        if persistence_engine is not None:
            ledger: InMemoryLedger | SqlLedger = SqlLedger(
                persistence_engine, account_id=ACCOUNT_ID, venue=VENUE
            )
        else:
            ledger = InMemoryLedger(account_id=ACCOUNT_ID, venue=VENUE)
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
        _oms = OrderManager(
            adapter=adapter,
            ledger=ledger,
            permits=permit_ledger(),
            instruments=_INSTRUMENTS,
            recorder=_record,
        )
        _ledger, _adapter, _oms_settings_id = ledger, adapter, id(settings)
        return _oms, ledger, adapter


def reset_oms_state() -> None:
    global _oms, _ledger, _adapter, _oms_settings_id
    _oms = _ledger = _adapter = None
    _oms_settings_id = None
    _start_of_day.clear()
    _peak.clear()


async def account_state(quote_asset: str) -> AccountState:
    """Conservative account view in one quote currency, marked at live mid prices.

    A held asset without a live mark makes exposure unknown: gross exposure is
    then infinite (blocking new risk) and equity is held at its last baseline so
    a missing quote is never mistaken for a loss.
    """

    _, ledger, _ = await components()
    balances = await ledger.balances()
    cash = balances[quote_asset].total if quote_asset in balances else Decimal("0")
    positions: dict[str, Decimal] = {}
    exposure = Decimal("0")
    marked = True
    for instrument_id, instrument in _INSTRUMENTS.items():
        if instrument.quote_asset != quote_asset:
            continue
        held = balances.get(instrument.base_asset)
        quantity = held.total if held is not None else Decimal("0")
        positions[instrument_id] = quantity
        if quantity == 0:
            continue
        quote = _quote(instrument_id)
        if quote is None:
            marked = False
            continue
        exposure += quantity * (quote.bid + quote.ask) / 2

    today = datetime.now(UTC).date()
    day, start = _start_of_day.get(quote_asset, (today, cash + exposure))
    if not marked:
        return AccountState(
            equity=start,
            start_of_day_equity=start,
            peak_equity=max(_peak.get(quote_asset, start), start),
            gross_exposure=Decimal("Infinity"),
            positions=positions,
        )
    equity = cash + exposure
    if day != today:
        day, start = today, equity
    _start_of_day[quote_asset] = (day, start)
    _peak[quote_asset] = max(_peak.get(quote_asset, equity), equity)
    return AccountState(
        equity=equity,
        start_of_day_equity=start,
        peak_equity=_peak[quote_asset],
        gross_exposure=exposure,
        positions=positions,
    )


def quote_asset_of(intent: TradeIntent) -> str:
    instrument = PAPER_INSTRUMENTS.get(intent.instrument_id)
    return instrument.quote_asset if instrument else "BRL"


async def reconcile() -> dict[str, object]:
    """Compare ledger totals with the venue; halt the venue on divergence."""

    from .risk_surface import governor

    _, ledger, adapter = await components()
    divergences = reconcile_balances(await ledger.balances(), await adapter.balances())
    if divergences:
        governor().trigger_kill(KillScope.VENUE, VENUE.value)
        await _record(
            "RECONCILIATION_DIVERGENCE",
            {"venue": VENUE.value, "divergences": [d.to_dict() for d in divergences]},
        )
    return {
        "venue": VENUE.value,
        "consistent": not divergences,
        "divergences": [d.to_dict() for d in divergences],
        "venue_halted": bool(divergences),
        **_BOUNDARY,
    }


async def execute(intent: TradeIntent, permit: RiskPermit) -> ManagedOrder | None:
    """Send an approved PAPER proposal to the paper venue, then reconcile."""

    if not settings.oms_paper_execution_enabled:
        return None
    oms, _, _ = await components()
    try:
        order = await oms.submit(intent, permit)
    except OrderRejected as exc:
        logger.warning("order refused: %s", exc.code)
        return None
    await reconcile()
    return order


@router.get("/orders")
async def list_orders(limit: int = 50) -> dict[str, object]:
    oms, _, _ = await components()
    orders = oms.orders[-max(1, min(limit, 500)) :]
    return {"orders": [o.to_dict() for o in reversed(orders)], **_BOUNDARY}


@router.get("/ledger")
async def ledger_view() -> dict[str, object]:
    _, ledger, _ = await components()
    balances = await ledger.balances()
    accounts = {}
    for quote_asset in _QUOTE_ASSETS:
        state = await account_state(quote_asset)
        accounts[quote_asset] = {
            "equity": str(state.equity),
            "start_of_day_equity": str(state.start_of_day_equity),
            "peak_equity": str(state.peak_equity),
            "gross_exposure": str(state.gross_exposure),
        }
    by_asset = defaultdict(dict)
    for asset, balance in balances.items():
        by_asset[asset] = balance.to_dict()
    return {
        "account_id": ACCOUNT_ID,
        "venue": VENUE.value,
        "mode": "PAPER",
        "balances": dict(by_asset),
        "accounts": accounts,
        **_BOUNDARY,
    }


@router.post("/reconcile")
async def reconcile_now() -> dict[str, object]:
    return await reconcile()


@router.post("/orders/{client_order_id}/resolve")
async def resolve_order(client_order_id: str) -> dict[str, object]:
    oms, _, _ = await components()
    if oms.get(client_order_id) is None:
        raise HTTPException(404, "unknown order")
    order = await oms.resolve_unknown(client_order_id)
    return {"order": order.to_dict(), **_BOUNDARY}


@router.post("/orders/{client_order_id}/cancel")
async def cancel_order(client_order_id: str) -> dict[str, object]:
    oms, _, _ = await components()
    if oms.get(client_order_id) is None:
        raise HTTPException(404, "unknown order")
    order = await oms.cancel(client_order_id)
    return {"order": order.to_dict(), **_BOUNDARY}
