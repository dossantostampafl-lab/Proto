from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import create_async_engine

from services.execution import (
    ExecutionReport,
    LedgerEntry,
    LedgerEntryKind,
    OrderStatus,
    OrderType,
    TradeIntent,
    VenueId,
)
from services.execution.catalog import PAPER_INSTRUMENTS
from services.execution.venue import PaperVenueAdapter, Quote
from services.oms import (
    InMemoryLedger,
    LedgerRejected,
    OrderManager,
    OrderRejected,
    SqlLedger,
    opening_balances,
    reconcile_balances,
)
from services.oms.ledger import entry_id
from services.risk import PermitLedger, PermitSigner

NOW = datetime(2026, 10, 13, 12, 0, tzinfo=UTC)
BTC_ID = "BINANCE_SPOT:BTCUSDT"
BTC = PAPER_INSTRUMENTS[BTC_ID]
QUOTE = Quote(bid=Decimal("62000.00"), ask=Decimal("62000.20"), observed_at=NOW)
SIGNER = PermitSigner(b"k" * 32)
OPENING = {"USDT": Decimal("10000")}


def intent(**overrides) -> TradeIntent:
    values = {
        "intent_id": uuid4(),
        "instrument_id": BTC_ID,
        "venue": VenueId.BINANCE_SPOT,
        "side": "BUY",
        "quantity": Decimal("0.01"),
        "order_type": OrderType.LIMIT,
        "limit_price": Decimal("62100.00"),
        "strategy_id": "hybrid-fusion-v1",
        "model_version": "fusion-v1",
        "signal_ids": (uuid4(),),
        "created_at": NOW,
    }
    values.update(overrides)
    return TradeIntent.model_validate(values)


def paper(balances=None, quote=QUOTE) -> PaperVenueAdapter:
    return PaperVenueAdapter(
        instruments=(BTC,),
        quotes=lambda _id: quote,
        balances=dict(OPENING if balances is None else balances),
        fee_bps=Decimal("10"),
        slippage_bps=Decimal("0"),
        clock=lambda: NOW,
        simulated_venue=VenueId.BINANCE_SPOT,
    )


async def setup(adapter=None, balances=None, events=None):
    ledger = InMemoryLedger(account_id="paper-1", venue=VenueId.BINANCE_SPOT)
    await ledger.post(
        opening_balances(
            dict(OPENING if balances is None else balances),
            account_id="paper-1",
            venue=VenueId.BINANCE_SPOT,
            now=NOW,
        )
    )

    async def record(event_type, payload):
        if events is not None:
            events.append((event_type, payload))

    oms = OrderManager(
        adapter=adapter or paper(balances),
        ledger=ledger,
        permits=PermitLedger(SIGNER),
        instruments=PAPER_INSTRUMENTS,
        recorder=record,
        clock=lambda: NOW,
    )
    return oms, ledger


async def test_buy_then_sell_keeps_ledger_and_venue_reconciled() -> None:
    adapter = paper()
    events: list = []
    oms, ledger = await setup(adapter, events=events)

    buy = intent()
    order = await oms.submit(buy, SIGNER.issue(buy, now=NOW))
    assert order.status is OrderStatus.FILLED
    assert order.history == ["NEW", "SUBMITTED", "FILLED"]
    balances = await ledger.balances()
    assert balances["BTC"].total == Decimal("0.01")
    assert balances["USDT"].reserved == 0
    assert balances["USDT"].total == Decimal("10000") - Decimal("620.002") - Decimal("0.620002")
    assert reconcile_balances(balances, await adapter.balances()) == []

    sell = intent(side="SELL", reduce_only=True, limit_price=Decimal("61900.00"))
    sold = await oms.submit(sell, SIGNER.issue(sell, now=NOW))
    assert sold.status is OrderStatus.FILLED
    balances = await ledger.balances()
    assert balances["BTC"].total == 0
    assert reconcile_balances(balances, await adapter.balances()) == []
    assert [e for e, _ in events] == [
        "ORDER_SUBMITTED",
        "ORDER_FILLED",
        "ORDER_SUBMITTED",
        "ORDER_FILLED",
    ]


async def test_insufficient_funds_rejects_before_reaching_the_venue() -> None:
    class Untouchable(PaperVenueAdapter):
        async def submit(self, order):
            raise AssertionError("venue must not be called")

    adapter = Untouchable(
        instruments=(BTC,),
        quotes=lambda _id: QUOTE,
        balances={},
        fee_bps=Decimal("10"),
        slippage_bps=Decimal("0"),
        simulated_venue=VenueId.BINANCE_SPOT,
    )
    oms, ledger = await setup(adapter, balances={"USDT": Decimal("100")})
    trade = intent()
    order = await oms.submit(trade, SIGNER.issue(trade, now=NOW))
    assert order.status is OrderStatus.REJECTED
    assert order.reason == "insufficient available USDT"
    assert (await ledger.balances())["USDT"].available == Decimal("100")


async def test_permits_are_required_single_use_and_bound() -> None:
    oms, _ = await setup()
    trade = intent()
    permit = SIGNER.issue(trade, now=NOW)
    await oms.submit(trade, permit)
    other = intent()
    with pytest.raises(OrderRejected, match="permit_intent_mismatch"):
        await oms.submit(other, permit)
    with pytest.raises(OrderRejected, match="permit_bad_signature"):
        await oms.submit(other, PermitSigner(b"x" * 32).issue(other, now=NOW))
    stale = SIGNER.issue(other, now=NOW - timedelta(seconds=11))
    with pytest.raises(OrderRejected, match="permit_expired"):
        await oms.submit(other, stale)


async def test_resubmitting_an_intent_returns_the_same_order() -> None:
    oms, ledger = await setup()
    trade = intent()
    first = await oms.submit(trade, SIGNER.issue(trade, now=NOW))
    again = await oms.submit(trade, SIGNER.issue(trade, now=NOW))
    assert again is first
    assert (await ledger.balances())["BTC"].total == Decimal("0.01")


class FlakyVenue(PaperVenueAdapter):
    """Executes the order but the response is lost on the way back."""

    async def submit(self, order):
        await super().submit(order)
        raise TimeoutError("response lost")


class LostVenue(PaperVenueAdapter):
    """The request never reached the venue."""

    async def submit(self, order):
        raise ConnectionError("connection reset")


def venue_of(cls):
    return cls(
        instruments=(BTC,),
        quotes=lambda _id: QUOTE,
        balances=dict(OPENING),
        fee_bps=Decimal("10"),
        slippage_bps=Decimal("0"),
        clock=lambda: NOW,
        simulated_venue=VenueId.BINANCE_SPOT,
    )


async def test_unknown_is_resolved_by_query_to_the_venue_fill() -> None:
    adapter = venue_of(FlakyVenue)
    oms, ledger = await setup(adapter)
    trade = intent()
    order = await oms.submit(trade, SIGNER.issue(trade, now=NOW))
    assert order.status is OrderStatus.UNKNOWN
    assert (await ledger.balances())["USDT"].reserved > 0

    resolved = await oms.resolve_unknown(order.client_order_id)
    assert resolved.status is OrderStatus.FILLED
    balances = await ledger.balances()
    assert balances["USDT"].reserved == 0
    assert reconcile_balances(balances, await adapter.balances()) == []


async def test_unknown_order_absent_at_venue_is_rejected_and_released() -> None:
    oms, ledger = await setup(venue_of(LostVenue))
    trade = intent()
    order = await oms.submit(trade, SIGNER.issue(trade, now=NOW))
    assert order.status is OrderStatus.UNKNOWN
    resolved = await oms.resolve_unknown(order.client_order_id)
    assert resolved.status is OrderStatus.REJECTED
    assert resolved.reason == "not found at venue"
    usdt = (await ledger.balances())["USDT"]
    assert usdt.reserved == 0 and usdt.available == Decimal("10000")


async def test_resting_limit_can_be_canceled_and_releases_the_hold() -> None:
    oms, ledger = await setup()
    trade = intent(limit_price=Decimal("60000.00"))
    order = await oms.submit(trade, SIGNER.issue(trade, now=NOW))
    assert order.status is OrderStatus.ACKNOWLEDGED
    assert (await ledger.balances())["USDT"].reserved == Decimal("603.00000000")
    canceled = await oms.cancel(order.client_order_id)
    assert canceled.status is OrderStatus.CANCELED
    assert (await ledger.balances())["USDT"].reserved == 0


async def test_partial_fills_post_increments_once() -> None:
    oms, ledger = await setup()
    trade = intent()
    order = await oms.submit(trade, SIGNER.issue(trade, now=NOW))
    assert order.status is OrderStatus.FILLED
    report = ExecutionReport(
        venue=VenueId.PAPER,
        client_order_id=order.client_order_id,
        instrument_id=BTC_ID,
        side="BUY",
        status=OrderStatus.FILLED,
        quantity=Decimal("0.01"),
        filled_quantity=Decimal("0.01"),
        average_price=Decimal("62000.20"),
        fee=Decimal("0.620002"),
        reported_at=NOW,
    )
    await oms._apply(order, report)  # duplicate report: no double posting
    assert (await ledger.balances())["BTC"].total == Decimal("0.01")


async def test_real_money_adapters_are_refused() -> None:
    class LiveAdapter(PaperVenueAdapter):
        @property
        def real_money_execution(self) -> bool:
            return True

    with pytest.raises(ValueError, match="real-money"):
        await setup(venue_of(LiveAdapter))


# --- ledger -------------------------------------------------------------------


def entry(kind: LedgerEntryKind, asset: str, amount: str, ref: str = "r") -> LedgerEntry:
    return LedgerEntry(
        entry_id=entry_id(ref, kind.value, asset, amount),
        account_id="paper-1",
        venue=VenueId.BINANCE_SPOT,
        asset=asset,
        amount=Decimal(amount),
        kind=kind,
        reference_id=ref,
        recorded_at=NOW,
    )


@pytest.fixture(params=["memory", "sql"])
async def ledger(request):
    if request.param == "memory":
        yield InMemoryLedger(account_id="paper-1", venue=VenueId.BINANCE_SPOT)
        return
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    sql = SqlLedger(engine, account_id="paper-1", venue=VenueId.BINANCE_SPOT)
    await sql.create_schema()
    yield sql
    await engine.dispose()


async def test_ledger_balances_reservations_and_idempotency(ledger) -> None:
    opening = entry(LedgerEntryKind.OPENING_BALANCE, "USDT", "1000")
    assert await ledger.post([opening]) is True
    assert await ledger.post([opening]) is False
    hold = entry(LedgerEntryKind.RESERVATION, "USDT", "-600")
    await ledger.post([hold])
    usdt = (await ledger.balances())["USDT"]
    assert (usdt.total, usdt.reserved, usdt.available) == (
        Decimal("1000"),
        Decimal("600"),
        Decimal("400"),
    )
    with pytest.raises(LedgerRejected, match="insufficient available USDT"):
        await ledger.post([entry(LedgerEntryKind.RESERVATION, "USDT", "-401")])
    with pytest.raises(LedgerRejected, match="release exceeds"):
        await ledger.post([entry(LedgerEntryKind.RELEASE, "USDT", "601")])
    with pytest.raises(LedgerRejected, match="partially overlaps"):
        await ledger.post([hold, entry(LedgerEntryKind.RELEASE, "USDT", "1")])
    assert (await ledger.balances())["USDT"].reserved == Decimal("600")


async def test_fills_are_recorded_even_past_available(ledger) -> None:
    await ledger.post([entry(LedgerEntryKind.TRADE, "USDT", "-5")])
    assert (await ledger.balances())["USDT"].total == Decimal("-5")


def test_reconciliation_reports_divergence_beyond_tolerance() -> None:
    from services.oms import summarize

    balances = summarize([entry(LedgerEntryKind.OPENING_BALANCE, "USDT", "1000")])
    assert reconcile_balances(balances, {"USDT": Decimal("1000")}) == []
    [divergence] = reconcile_balances(balances, {"USDT": Decimal("999"), "BTC": Decimal("0")})
    assert divergence.asset == "USDT" and divergence.difference == Decimal("-1")
    assert (
        reconcile_balances(balances, {"USDT": Decimal("999")}, tolerances={"USDT": Decimal("2")})
        == []
    )


def test_opening_balance_must_be_positive() -> None:
    with pytest.raises(ValueError, match="positive"):
        entry(LedgerEntryKind.OPENING_BALANCE, "USDT", "-1")
