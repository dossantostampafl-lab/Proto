from uuid import uuid4

from apps.api.app.app_state import portfolio
from apps.api.app.models import Asset, Fill, Side, SimulationOrder
from apps.api.app.research import _portfolio_gauges, prometheus_metrics, runtime_metrics


def _apply_fill() -> None:
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


def test_runtime_metrics_embeds_canonical_portfolio_snapshot() -> None:
    portfolio.reset()
    try:
        _apply_fill()
        body = runtime_metrics()
        snapshot = body["portfolio"]
        assert isinstance(snapshot, dict)
        assert snapshot["gross_exposure"] == 100.0
        assert snapshot["turnover_notional"] == 100.0
        assert snapshot["execution_costs"]["slippage"] == 0.1
        assert snapshot["temporal_exposure_notional_seconds"] >= 0.0
        assert snapshot["max_position_age_seconds"] >= 0.0
    finally:
        portfolio.reset()


def test_portfolio_gauges_export_risk_pnl_execution_and_time_state() -> None:
    portfolio.reset()
    try:
        _apply_fill()
        gauges = _portfolio_gauges()
        assert gauges["portfolio_gross_exposure"] == 100.0
        assert gauges["portfolio_net_exposure"] == 100.0
        assert gauges["portfolio_total_pnl_after_fees"] == -0.1
        assert gauges["portfolio_realized_drawdown"] == 0.1
        assert gauges["portfolio_max_asset_concentration"] == 1.0
        assert gauges["portfolio_turnover_notional"] == 100.0
        assert gauges["portfolio_slippage_cost"] == 0.1
        assert gauges["portfolio_temporal_exposure_notional_seconds"] >= 0.0
        assert gauges["portfolio_max_position_age_seconds"] >= 0.0
    finally:
        portfolio.reset()


def test_prometheus_surface_contains_portfolio_gauges() -> None:
    portfolio.reset()
    try:
        _apply_fill()
        output = prometheus_metrics()
        assert "proto_portfolio_gross_exposure 100.0" in output
        assert "proto_portfolio_total_pnl_after_fees -0.1" in output
        assert "proto_portfolio_realized_drawdown 0.1" in output
        assert "proto_portfolio_turnover_notional 100.0" in output
        assert "proto_portfolio_slippage_cost 0.1" in output
        assert "# TYPE proto_portfolio_temporal_exposure_notional_seconds gauge" in output
        assert "proto_portfolio_temporal_exposure_notional_seconds " in output
        assert "# TYPE proto_portfolio_max_position_age_seconds gauge" in output
        assert "proto_portfolio_max_position_age_seconds " in output
        assert "# TYPE proto_ws_oversized_messages_total counter" in output
    finally:
        portfolio.reset()
