from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from apps.api.app import app_state
from apps.api.app.models import Asset, Fill, Side, SimulationOrder
from apps.api.app.persistence import (
    AsyncSqlFillJournal,
    build_engine,
    init_database,
    register_portfolio_recovery_target,
)
from apps.api.app.portfolio import PaperPortfolio
from apps.api.app.portfolio_recovery import recover_paper_portfolio


async def _entries():
    now = datetime.now(UTC)
    values = [
        (uuid4(), "BUY", 2.0, 100.0, 1.0, now),
        (uuid4(), "SELL", 1.0, 110.0, 0.5, now + timedelta(seconds=1)),
    ]
    for order_id, side, quantity, price, fee, filled_at in values:
        yield {
            "order_id": str(order_id),
            "market_id": "btc-paper",
            "asset": "BTC",
            "side": side,
            "filled_quantity": quantity,
            "fill_price": price,
            "fee": fee,
            "slippage_bps": 2.0,
            "filled_at": filled_at.isoformat(),
        }


def _eth_order_and_fill() -> tuple[SimulationOrder, Fill]:
    order_id = uuid4()
    filled_at = datetime.now(UTC)
    order = SimulationOrder(
        id=order_id,
        market_id="eth-paper",
        asset=Asset.ETH,
        side=Side.BUY,
        quantity=3.0,
        limit_price=200.0,
        created_at=filled_at,
    )
    fill = Fill(
        order_id=order_id,
        market_id=order.market_id,
        asset=order.asset,
        side=order.side,
        filled_quantity=3.0,
        fill_price=200.0,
        fee=1.2,
        slippage_bps=1.5,
        filled_at=filled_at,
    )
    return order, fill


@pytest.mark.asyncio
async def test_recover_paper_portfolio_replays_chronologically() -> None:
    portfolio = PaperPortfolio()

    recovered = await recover_paper_portfolio(portfolio, _entries())

    assert recovered == 2
    snapshot = portfolio.snapshot()
    position = snapshot["positions"][0]
    assert position["asset"] == "BTC"
    assert position["quantity"] == 1.0
    assert position["average_price"] == 100.0
    assert position["realized_pnl"] == 10.0
    assert position["fees"] == 1.5


@pytest.mark.asyncio
async def test_init_database_restores_registered_portfolio_from_durable_fills() -> None:
    engine = build_engine("sqlite+aiosqlite:///:memory:")
    target = PaperPortfolio()
    register_portfolio_recovery_target(target)
    try:
        await init_database(engine)
        journal = AsyncSqlFillJournal(engine)
        order, fill = _eth_order_and_fill()
        await journal.append(order, fill)

        target.reset()
        assert target.snapshot()["positions"] == []

        await init_database(engine)

        position = target.snapshot()["positions"][0]
        assert position["asset"] == "ETH"
        assert position["quantity"] == 3.0
        assert position["average_price"] == 200.0
    finally:
        register_portfolio_recovery_target(app_state.portfolio)
        await engine.dispose()


@pytest.mark.asyncio
async def test_new_session_prevents_prior_fills_from_reappearing_after_restart() -> None:
    engine = build_engine("sqlite+aiosqlite:///:memory:")
    target = PaperPortfolio()
    register_portfolio_recovery_target(target)
    try:
        await init_database(engine)
        journal = AsyncSqlFillJournal(engine)
        order, fill = _eth_order_and_fill()
        await journal.append(order, fill)
        await init_database(engine)
        assert target.snapshot()["positions"][0]["quantity"] == 3.0

        await journal.start_new_session()
        target.reset()
        await init_database(engine)

        assert target.snapshot()["positions"] == []
        assert await journal.list(limit=10) == []
    finally:
        register_portfolio_recovery_target(app_state.portfolio)
        await engine.dispose()
