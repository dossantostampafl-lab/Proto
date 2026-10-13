"""Phase 4 paper runtime: managed reconciliation and real ledger risk state."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from decimal import Decimal
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import create_async_engine

from services.execution import OrderStatus, OrderType, TradableInstrument, TradeIntent, VenueId
from services.execution.oms import OrderRejected, oms_metadata
from services.execution.paper_runtime import Phase4PaperRuntime
from services.execution.venue import Quote
from services.market_data.instruments import AssetClass, SessionType
from services.risk import RiskEngine, RiskGovernor
from services.risk.durable_permits import permit_metadata
from services.risk.permits import PermitSigner

NOW = datetime(2026, 10, 13, 16, 0, tzinfo=UTC)
KEY = b"p" * 32
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


@pytest.fixture
async def database(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'runtime.db'}")
    async with engine.begin() as conn:
        await conn.run_sync(oms_metadata.create_all)
        await conn.run_sync(permit_metadata.create_all)
    yield engine
    await engine.dispose()


def trade(amount: str = "0.003") -> TradeIntent:
    return TradeIntent(
        intent_id=uuid4(),
        instrument_id=BTC.instrument_id,
        venue=VenueId.PAPER,
        side="BUY",
        quantity=Decimal(amount),
        order_type=OrderType.LIMIT,
        limit_price=Decimal("350100"),
        strategy_id="hybrid-fusion-v1",
        model_version="v1",
        signal_ids=(uuid4(),),
        created_at=NOW,
    )


def runtime(engine, signer):
    return Phase4PaperRuntime(
        engine=engine,
        signer=signer,
        instruments=(BTC,),
        quotes=lambda instrument_id: QUOTE,
        starting_balances={"BRL": Decimal("10000")},
        fee_bps=Decimal("10"),
        slippage_bps=Decimal("0"),
        reconciliation_interval_seconds=0.01,
    )


async def test_runtime_authorized_paper_fill_uses_ledger_for_next_risk_decision(database):
    signer = PermitSigner(KEY)
    service = runtime(database, signer)
    await service.start(now=NOW)
    assert service.running
    assert service.real_money_execution is False

    try:
        governor = RiskGovernor(RiskEngine(instruments={BTC.instrument_id: BTC}, signer=signer))
        first = trade()
        account = await service.risk_account(now=NOW)
        decision = governor.assess(first, account=account, quote=QUOTE, now=NOW)
        assert decision.approved and decision.permit is not None
        report = await service.execute_authorized(first, decision.permit, now=NOW)
        assert report.status is OrderStatus.FILLED
        updated = await service.risk_account(now=NOW)
        assert updated.positions[BTC.instrument_id] == Decimal("0.003")
        assert updated.equity < Decimal("10000")
        assert updated.gross_exposure > Decimal("0")
    finally:
        await service.stop()
    assert not service.running


async def test_runtime_stops_new_orders_after_detected_balance_divergence(database):
    signer = PermitSigner(KEY)
    service = runtime(database, signer)
    await service.start(now=NOW)
    try:
        service.adapter._balances["BRL"] -= Decimal("5")
        outcome = await service.oms.reconcile(tolerance=Decimal("0.01"))
        assert not outcome["ok"]
        request = trade()
        with pytest.raises(OrderRejected, match="venue_halted"):
            await service.execute_authorized(
                request, signer.issue(request, now=NOW), now=NOW
            )
    finally:
        await service.stop()


async def test_reconciliation_supervisor_runs_and_stops_cleanly(database):
    signer = PermitSigner(KEY)
    service = runtime(database, signer)
    await service.start(now=NOW)
    await asyncio.sleep(0.04)
    assert service.running
    await service.stop()
    assert not service.running
    assert service.background_failure is None
