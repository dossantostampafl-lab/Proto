from decimal import Decimal
from uuid import uuid4

from apps.api.app.models import Asset, Fill, Side, SimulationOrder
from apps.api.app.portfolio import PaperPortfolio


def _apply(
    portfolio: PaperPortfolio,
    *,
    side: Side,
    quantity: float,
    price: float,
    fee: float = 0.0,
    slippage_bps: float = 0.0,
) -> None:
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
    assert portfolio.apply_fill(order, fill) is True


def test_active_portfolio_accumulates_fractional_fills_without_binary_float_dust() -> None:
    portfolio = PaperPortfolio()

    for _ in range(10):
        _apply(portfolio, side=Side.BUY, quantity=0.1, price=0.1)

    snapshot = portfolio.snapshot({Asset.BTC: 0.1})

    assert snapshot["positions"][0]["quantity"] == 1.0
    assert snapshot["turnover_notional"] == 0.1
    assert portfolio._positions[Asset.BTC].quantity == Decimal("1.0")
    assert portfolio._turnover_notional == Decimal("0.10")


def test_active_portfolio_keeps_fees_and_slippage_exact_internally() -> None:
    portfolio = PaperPortfolio()

    _apply(
        portfolio,
        side=Side.BUY,
        quantity=0.1,
        price=100.0,
        fee=0.1,
        slippage_bps=10.0,
    )
    _apply(
        portfolio,
        side=Side.BUY,
        quantity=0.2,
        price=100.0,
        fee=0.2,
        slippage_bps=10.0,
    )

    snapshot = portfolio.snapshot({Asset.BTC: 100.0})

    assert snapshot["positions"][0]["quantity"] == 0.3
    assert snapshot["total_fees"] == 0.3
    assert snapshot["execution_costs"]["slippage"] == 0.03
    assert portfolio._positions[Asset.BTC].fees == Decimal("0.3")
    assert portfolio._slippage_cost == Decimal("0.030")
