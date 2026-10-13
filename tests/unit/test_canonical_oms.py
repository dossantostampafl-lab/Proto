"""Phase 4 OMS regression contract: permit -> order -> durable ledger -> reconcile."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import create_async_engine

from services.execution import (
    OrderStatus,
    OrderType,
    ReleaseStage,
    TimeInForce,
    TradableInstrument,
    TradeIntent,
    VenueId,
)
from services.execution.oms import (
    CanonicalPaperOMS,
    OrderRejected,
    balance_entries,
    fill_records,
    oms_metadata,
    order_records,
    reservations,
)
from services.execution.venue import PaperVenueAdapter, Quote
from services.market_data.instruments import AssetClass, SessionType
from services.risk.durable_permits import consumed_permits, permit_metadata
from services.risk.permits import PermitSigner

NOW = datetime(2026, 10, 13, 16, 0, tzinfo=UTC)
KEY = b"q" * 32
BTC = TradableInstrument(
    instrument_id="PAPER:BTCBRL",
    venue=VenueId.PAPER,
    asset_class=AssetClass.CRYPTO,
    base_asset="BTC",
    quote_asset="BRL",
    tick_size=Decimal("1"),
    lot_size=Decimal("0.00001"),
    min_quantity=Decimal("0.00001"),
    min_notional=Decimal("10"),
    session_type=SessionType.CONTINUOUS_24_7,
    timezone="UTC",
)
QUOTE = Quote(bid=Decimal("349990"), ask=Decimal("350010"), observed_at=NOW)


def intent(*, price="350100", amount="0.01", stage=ReleaseStage.PAPER, tif=TimeInForce.GTC):
    return TradeIntent(
        intent_id=uuid4(),
        instrument_id=BTC.instrument_id,
        venue=VenueId.PAPER,
        side="BUY",
        quantity=Decimal(amount),
        order_type=OrderType.LIMIT,
        limit_price=Decimal(price),
        time_in_force=tif,
        strategy_id="integration",
        model_version="v1",
        signal_ids=(uuid4(),),
        stage=stage,
        created_at=NOW,
    )


def venue(*, initial="10000"):
    return PaperVenueAdapter(
        instruments=(BTC,),
        quotes=lambda _: QUOTE,
        balances={"BRL": Decimal(initial), "BTC": Decimal("0")},
        fee_bps=Decimal("10"),
        slippage_bps=Decimal("0"),
        clock=lambda: NOW,
    )


@pytest.fixture
async def database(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'oms.db'}")
    async with engine.begin() as connection:
        await connection.run_sync(oms_metadata.create_all)
        await connection.run_sync(permit_metadata.create_all)
    yield engine
    await engine.dispose()


def oms(engine, broker, signer):
    return CanonicalPaperOMS(engine, broker, signer, instruments={BTC.instrument_id: BTC})


async def test_paper_fill_is_one_durable_order_with_balanced_trade_and_fee_entries(database):
    signer = PermitSigner(KEY)
    broker = venue()
    manager = oms(database, broker, signer)
    request = intent()
    report = await manager.place(request, signer.issue(request, now=NOW), now=NOW)
    assert report.status is OrderStatus.FILLED
    async with database.connect() as conn:
        rows = (await conn.execute(select(order_records))).mappings().all()
        entries = (await conn.execute(select(balance_entries))).mappings().all()
        fills = (await conn.execute(select(fill_records))).mappings().all()
        reserved = (await conn.execute(select(reservations))).mappings().all()
    assert len(rows) == 1
    assert len(fills) == 1
    assert Decimal(fills[0]["delta_quantity"]) == Decimal("0.01")
    assert Decimal(fills[0]["delta_notional"]) == Decimal("3500.10")
    assert rows[0]["status"] == OrderStatus.FILLED.value
    amounts = [(e["asset"], e["kind"], Decimal(e["amount"])) for e in entries]
    assert ("BTC", "TRADE", Decimal("0.01")) in amounts
    assert ("BRL", "TRADE", Decimal("-3500.10")) in amounts
    assert ("BRL", "FEE", Decimal("-3.50010")) in amounts
    assert len(reserved) == 1 and Decimal(reserved[0]["remaining"]) == 0
    assert (await manager.reconcile())["ok"] is True


async def test_repeated_request_after_restart_does_not_resubmit_or_duplicate_ledger(database):
    signer = PermitSigner(KEY)
    broker = venue()
    request = intent()
    first = await oms(database, broker, signer).place(
        request, signer.issue(request, now=NOW), now=NOW
    )
    second = await oms(database, broker, signer).place(
        request, signer.issue(request, now=NOW), now=NOW
    )
    assert first == second
    assert (await broker.balances())["BTC"] == Decimal("0.01")
    async with database.connect() as conn:
        assert len((await conn.execute(select(consumed_permits))).all()) == 1
        assert len((await conn.execute(select(balance_entries))).all()) == 3


async def test_live_or_canary_never_reaches_paper_adapter(database):
    signer = PermitSigner(KEY)
    broker = venue()
    manager = oms(database, broker, signer)
    for stage in (ReleaseStage.CANARY, ReleaseStage.LIVE, ReleaseStage.SHADOW):
        request = intent(stage=stage)
        with pytest.raises(OrderRejected, match="paper_only"):
            await manager.place(request, signer.issue(request, now=NOW), now=NOW)
    async with database.connect() as conn:
        assert len((await conn.execute(select(consumed_permits))).all()) == 0


async def test_unknown_submit_never_resends_and_query_resolves(database):
    class LostAcknowledgment(PaperVenueAdapter):
        async def submit(self, order):
            await super().submit(order)
            raise TimeoutError("acknowledgment lost")

    signer = PermitSigner(KEY)
    broker = LostAcknowledgment(
        instruments=(BTC,),
        quotes=lambda _: QUOTE,
        balances={"BRL": Decimal("10000"), "BTC": Decimal("0")},
        fee_bps=Decimal("10"),
        slippage_bps=Decimal("0"),
        clock=lambda: NOW,
    )
    manager = oms(database, broker, signer)
    request = intent()
    unknown = await manager.place(request, signer.issue(request, now=NOW), now=NOW)
    assert unknown.status is OrderStatus.UNKNOWN
    recovered = await oms(database, broker, signer).resolve_unknown(request.client_order_id(0))
    assert recovered.status is OrderStatus.FILLED
    assert (await broker.balances())["BTC"] == Decimal("0.01")
    async with database.connect() as conn:
        assert len((await conn.execute(select(balance_entries))).all()) == 3


async def test_reservation_blocks_double_spend_until_cancel(database):
    signer = PermitSigner(KEY)
    broker = venue()
    manager = oms(database, broker, signer)
    first = intent(price="340000", amount="0.02")
    second = intent(price="340000", amount="0.02")
    resting = await manager.place(first, signer.issue(first, now=NOW), now=NOW)
    assert resting.status is OrderStatus.ACKNOWLEDGED
    with pytest.raises(OrderRejected, match="insufficient_available_balance"):
        await manager.place(second, signer.issue(second, now=NOW), now=NOW)
    canceled = await manager.cancel(first.client_order_id(0))
    assert canceled.status is OrderStatus.CANCELED
    accepted = await manager.place(second, signer.issue(second, now=NOW), now=NOW)
    assert accepted.status is OrderStatus.ACKNOWLEDGED


async def test_reconciliation_drift_halts_all_new_venue_orders(database):
    signer = PermitSigner(KEY)
    broker = venue()
    manager = oms(database, broker, signer)
    first = intent()
    await manager.place(first, signer.issue(first, now=NOW), now=NOW)
    broker._balances["BRL"] -= Decimal("1")
    result = await manager.reconcile(tolerance=Decimal("0.01"))
    assert result["ok"] is False
    assert "BRL" in result["differences"]
    second = intent()
    with pytest.raises(OrderRejected, match="venue_halted"):
        await manager.place(second, signer.issue(second, now=NOW), now=NOW)


async def test_unknown_report_itself_halts_venue_before_next_order(database):
    class UncertainPaper(PaperVenueAdapter):
        async def submit(self, order):
            return self._report(order, OrderStatus.UNKNOWN)

    signer = PermitSigner(KEY)
    broker = UncertainPaper(
        instruments=(BTC,),
        quotes=lambda _: QUOTE,
        balances={"BRL": Decimal("10000"), "BTC": Decimal("0")},
        fee_bps=Decimal("10"),
        slippage_bps=Decimal("0"),
        clock=lambda: NOW,
    )
    manager = oms(database, broker, signer)
    first = intent()
    result = await manager.place(first, signer.issue(first, now=NOW), now=NOW)
    assert result.status is OrderStatus.UNKNOWN
    second = intent()
    with pytest.raises(OrderRejected, match="venue_halted"):
        await manager.place(second, signer.issue(second, now=NOW), now=NOW)


async def test_ioc_intent_is_not_left_as_a_resting_reservation(database):
    signer = PermitSigner(KEY)
    broker = venue()
    manager = oms(database, broker, signer)
    request = intent(price="340000", tif=TimeInForce.IOC)
    result = await manager.place(request, signer.issue(request, now=NOW), now=NOW)
    assert result.status is OrderStatus.CANCELED
    async with database.connect() as connection:
        row = (await connection.execute(select(reservations))).mappings().one()
    assert Decimal(row["remaining"]) == 0


async def test_periodic_reconciliation_detects_drift_and_stops_on_signal(database):
    signer = PermitSigner(KEY)
    broker = venue()
    manager = oms(database, broker, signer)
    request = intent()
    await manager.place(request, signer.issue(request, now=NOW), now=NOW)
    broker._balances["BRL"] -= Decimal("5")
    stop = asyncio.Event()
    checked = 0
    original = manager.reconcile

    async def one_cycle(*, tolerance=Decimal("0")):
        nonlocal checked
        checked += 1
        result = await original(tolerance=tolerance)
        stop.set()
        return result

    manager.reconcile = one_cycle
    await manager.reconciliation_loop(stop, interval_seconds=0.01)
    assert checked == 1
    async with database.connect() as connection:
        from services.execution.oms import venue_halts

        halt = (await connection.execute(select(venue_halts))).mappings().one()
    assert halt["reason"] == "balance_divergence"


async def test_partial_fill_is_refreshed_without_resubmission(database):
    class DelayedSecondFill(PaperVenueAdapter):
        async def submit(self, order):
            completed = await super().submit(order)
            assert completed.status is OrderStatus.FILLED
            return completed.model_copy(
                update={
                    "status": OrderStatus.PARTIALLY_FILLED,
                    "filled_quantity": Decimal("0.004"),
                    "fee": Decimal("1.40004"),
                }
            )

    signer = PermitSigner(KEY)
    broker = DelayedSecondFill(
        instruments=(BTC,),
        quotes=lambda _: QUOTE,
        balances={"BRL": Decimal("10000"), "BTC": Decimal("0")},
        fee_bps=Decimal("10"),
        slippage_bps=Decimal("0"),
        clock=lambda: NOW,
    )
    manager = oms(database, broker, signer)
    request = intent()
    partial = await manager.place(request, signer.issue(request, now=NOW), now=NOW)
    assert partial.status is OrderStatus.PARTIALLY_FILLED
    completed = await manager.refresh(request.client_order_id(0))
    assert completed.status is OrderStatus.FILLED
    assert (await manager.reconcile())["ok"] is True
    async with database.connect() as connection:
        fills = (await connection.execute(select(fill_records))).mappings().all()
    assert len(fills) == 2
    assert sum((Decimal(f["delta_quantity"]) for f in fills), Decimal("0")) == Decimal("0.01")


async def test_fee_aware_reservation_rejects_order_before_consuming_permit(database):
    signer = PermitSigner(KEY)
    broker = venue(initial="3502")
    manager = oms(database, broker, signer)
    request = intent(price="350100")
    with pytest.raises(OrderRejected, match="insufficient_available_balance"):
        await manager.place(request, signer.issue(request, now=NOW), now=NOW)
    assert (await broker.balances())["BRL"] == Decimal("3502")
    async with database.connect() as connection:
        assert (await connection.execute(select(consumed_permits))).all() == []
        assert (await connection.execute(select(order_records))).all() == []


async def test_expired_risk_permit_never_submits_order(database):
    signer = PermitSigner(KEY)
    broker = venue()
    manager = oms(database, broker, signer)
    request = intent()
    with pytest.raises(Exception, match="expired"):
        await manager.place(
            request,
            signer.issue(request, now=NOW),
            now=NOW + timedelta(seconds=11),
        )
    async with database.connect() as connection:
        assert (await connection.execute(select(consumed_permits))).all() == []


async def test_unknown_order_never_resubmitted_while_venue_unavailable(database):
    class UnreachableVenue(PaperVenueAdapter):
        submit_count = 0

        async def submit(self, order):
            self.submit_count += 1
            raise TimeoutError("venue unreachable")

        async def query(self, client_order_id):
            raise TimeoutError("still unavailable")

    signer = PermitSigner(KEY)
    broker = UnreachableVenue(
        instruments=(BTC,),
        quotes=lambda _: QUOTE,
        balances={"BRL": Decimal("10000"), "BTC": Decimal("0")},
        fee_bps=Decimal("10"),
        slippage_bps=Decimal("0"),
        clock=lambda: NOW,
    )
    request = intent()
    manager = oms(database, broker, signer)
    permit = signer.issue(request, now=NOW)
    first = await manager.place(request, permit, now=NOW)
    assert first.status is OrderStatus.UNKNOWN
    second = await manager.place(request, permit, now=NOW)
    assert second.status is OrderStatus.UNKNOWN
    assert broker.submit_count == 1


async def test_position_can_be_sold_when_initial_snapshot_omits_zero_base_asset(database):
    signer = PermitSigner(KEY)
    broker = PaperVenueAdapter(
        instruments=(BTC,),
        quotes=lambda _: QUOTE,
        balances={"BRL": Decimal("10000")},
        fee_bps=Decimal("10"),
        slippage_bps=Decimal("0"),
        clock=lambda: NOW,
    )
    manager = oms(database, broker, signer)
    buy = intent()
    buy_report = await manager.place(buy, signer.issue(buy, now=NOW), now=NOW)
    assert buy_report.status is OrderStatus.FILLED
    sell = intent(price="349900")
    sell = sell.model_copy(update={"side": "SELL", "reduce_only": True})
    sell_report = await manager.place(sell, signer.issue(sell, now=NOW), now=NOW)
    assert sell_report.status is OrderStatus.FILLED
    assert (await manager.reconcile())["ok"] is True
