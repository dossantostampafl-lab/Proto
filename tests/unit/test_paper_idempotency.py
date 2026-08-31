from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from apps.api.app.main import app
from apps.api.app.models import Asset, Fill, Side, SimulationOrder
from apps.api.app.persistence import AsyncSqlFillJournal, build_engine, init_database
from apps.api.app.portfolio import PaperPortfolio


def _order_and_fill():
    order_id = uuid4()
    now = datetime.now(UTC)
    order = SimulationOrder(
        id=order_id,
        market_id="btc-paper",
        asset=Asset.BTC,
        side=Side.BUY,
        quantity=1.0,
        limit_price=100.0,
        created_at=now,
    )
    fill = Fill(
        order_id=order_id,
        market_id=order.market_id,
        asset=order.asset,
        side=order.side,
        filled_quantity=1.0,
        fill_price=100.0,
        fee=0.2,
        slippage_bps=2.0,
        filled_at=now,
    )
    return order, fill


def test_paper_portfolio_applies_an_order_only_once() -> None:
    portfolio = PaperPortfolio()
    order, fill = _order_and_fill()

    assert portfolio.apply_fill(order, fill) is True
    first = portfolio.snapshot()
    assert portfolio.apply_fill(order, fill) is False

    assert portfolio.snapshot() == first
    assert len(portfolio.journal()) == 1
    assert portfolio.has_order(order.id) is True


@pytest.mark.asyncio
async def test_sql_journal_reports_duplicate_order_without_second_row() -> None:
    engine = build_engine("sqlite+aiosqlite:///:memory:")
    try:
        await init_database(engine)
        journal = AsyncSqlFillJournal(engine)
        order, fill = _order_and_fill()

        assert await journal.append(order, fill) is True
        assert await journal.append(order, fill) is False
        assert len(await journal.list()) == 1
    finally:
        await engine.dispose()


def test_simulation_api_duplicate_order_does_not_double_portfolio() -> None:
    client = TestClient(app)
    client.post("/simulation/reset")
    order_id = str(uuid4())
    payload = {
        "order": {
            "id": order_id,
            "market_id": "btc-usd-paper",
            "asset": "BTC",
            "side": "BUY",
            "quantity": 0.01,
            "limit_price": 61_000,
        },
        "snapshot": {
            "symbol": "BTC",
            "market_id": "btc-usd-paper",
            "bid": 60_000,
            "ask": 60_010,
            "market_probability": 0.52,
        },
    }

    first = client.post("/v1/simulate", json=payload)
    second = client.post("/v1/simulate", json=payload)

    assert first.status_code == 200
    assert second.status_code == 200
    portfolio = client.get("/v1/portfolio").json()
    fills = client.get("/v1/fills").json()
    assert portfolio["positions"][0]["quantity"] == 0.01
    assert fills["count"] == 1
