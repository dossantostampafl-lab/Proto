from uuid import uuid4

from apps.api.app.models import Asset, Fill, Side, SimulationOrder
from apps.api.app.portfolio import PaperPortfolio


def _fill(*, side: Side, quantity: float, price: float, fee: float, slippage_bps: float) -> tuple[SimulationOrder, Fill]:
    order_id = uuid4()
    order = SimulationOrder(
        id=order_id,
        market_id="btc-paper",
        asset=Asset.BTC,
        side=side,
        quantity=quantity,
        limit_price=price,
    )
    fill = Fill(
        order_id=order_id,
        market_id=order.market_id,
        asset=order.asset,
        side=side,
        filled_quantity=quantity,
        fill_price=price,
        fee=fee,
        slippage_bps=slippage_bps,
    )
    return order, fill


def test_snapshot_tracks_turnover_and_execution_costs() -> None:
    portfolio = PaperPortfolio()
    first_order, first_fill = _fill(
        side=Side.BUY,
        quantity=2.0,
        price=100.0,
        fee=0.20,
        slippage_bps=10.0,
    )
    second_order, second_fill = _fill(
        side=Side.SELL,
        quantity=1.0,
        price=110.0,
        fee=0.11,
        slippage_bps=20.0,
    )

    assert portfolio.apply_fill(first_order, first_fill) is True
    assert portfolio.apply_fill(second_order, second_fill) is True

    snapshot = portfolio.snapshot({Asset.BTC: 110.0})

    assert snapshot["turnover_notional"] == 310.0
    assert snapshot["execution_costs"] == {
        "fees": 0.31,
        "slippage": 0.42,
        "total": 0.73,
    }


def test_duplicate_fill_does_not_double_count_execution_costs() -> None:
    portfolio = PaperPortfolio()
    order, fill = _fill(
        side=Side.BUY,
        quantity=1.0,
        price=100.0,
        fee=0.10,
        slippage_bps=10.0,
    )

    assert portfolio.apply_fill(order, fill) is True
    assert portfolio.apply_fill(order, fill) is False

    snapshot = portfolio.snapshot()
    assert snapshot["turnover_notional"] == 100.0
    assert snapshot["execution_costs"] == {
        "fees": 0.1,
        "slippage": 0.1,
        "total": 0.2,
    }


def test_reset_clears_execution_attribution() -> None:
    portfolio = PaperPortfolio()
    order, fill = _fill(
        side=Side.BUY,
        quantity=1.0,
        price=100.0,
        fee=0.10,
        slippage_bps=10.0,
    )
    portfolio.apply_fill(order, fill)

    portfolio.reset()

    snapshot = portfolio.snapshot()
    assert snapshot["turnover_notional"] == 0.0
    assert snapshot["execution_costs"] == {
        "fees": 0.0,
        "slippage": 0.0,
        "total": 0.0,
    }
