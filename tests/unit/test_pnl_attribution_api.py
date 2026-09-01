from uuid import uuid4

from fastapi.testclient import TestClient

from apps.api.app.app_state import portfolio
from apps.api.app.main import app
from apps.api.app.models import Asset, Fill, Side, SimulationOrder

client = TestClient(app)


def _apply_costed_fill() -> None:
    order_id = uuid4()
    order = SimulationOrder(
        id=order_id,
        market_id="btc-paper",
        asset=Asset.BTC,
        side=Side.BUY,
        quantity=1.0,
        limit_price=100.0,
    )
    fill = Fill(
        order_id=order_id,
        market_id=order.market_id,
        asset=order.asset,
        side=order.side,
        filled_quantity=1.0,
        fill_price=100.0,
        fee=0.1,
        slippage_bps=10.0,
    )
    assert portfolio.apply_fill(order, fill) is True


def test_pnl_attribution_reports_only_accounting_components_as_known() -> None:
    portfolio.reset()
    try:
        _apply_costed_fill()
        response = client.get("/pnl/attribution")
        body = response.json()

        assert response.status_code == 200
        assert body["policy"] == "KNOWN_ACCOUNTING_COSTS_ONLY"
        assert body["known_components"] == ["fees", "slippage"]
        assert body["unresolved_components_are_residual"] is True
        assert body["fees"] == -0.1
        assert body["slippage"] == -0.1
        assert body["model_edge"] == 0.0
        assert body["market_movement"] == 0.0
        assert body["residual"] == 0.1
        assert body["attributed_total"] == body["observed_total_pnl"]
        assert body["observed_total_pnl"] == -0.1
        assert body["real_money_execution"] is False
    finally:
        portfolio.reset()


def test_empty_portfolio_attribution_reconciles_to_zero() -> None:
    portfolio.reset()
    response = client.get("/pnl/attribution")
    body = response.json()

    assert response.status_code == 200
    assert body["fees"] == 0.0
    assert body["slippage"] == 0.0
    assert body["residual"] == 0.0
    assert body["attributed_total"] == 0.0
    assert body["observed_total_pnl"] == 0.0
