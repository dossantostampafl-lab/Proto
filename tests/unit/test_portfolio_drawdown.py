from uuid import uuid4

from apps.api.app.models import Asset, Fill, Side, SimulationOrder
from apps.api.app.portfolio import PaperPortfolio


def _trade(
    *,
    side: Side,
    quantity: float,
    price: float,
    fee: float = 0.0,
) -> tuple[SimulationOrder, Fill]:
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
        slippage_bps=0.0,
    )
    return order, fill


def test_drawdown_tracks_peak_to_trough_realized_pnl_after_fees() -> None:
    portfolio = PaperPortfolio()
    trades = [
        _trade(side=Side.BUY, quantity=1.0, price=100.0),
        _trade(side=Side.SELL, quantity=1.0, price=120.0),
        _trade(side=Side.BUY, quantity=1.0, price=100.0),
        _trade(side=Side.SELL, quantity=1.0, price=90.0),
    ]

    for order, fill in trades:
        assert portfolio.apply_fill(order, fill) is True

    snapshot = portfolio.snapshot()
    assert snapshot["total_realized_pnl"] == 10.0
    assert snapshot["realized_pnl_high_watermark"] == 20.0
    assert snapshot["realized_drawdown"] == 10.0


def test_fees_participate_in_realized_drawdown() -> None:
    portfolio = PaperPortfolio()
    opening = _trade(side=Side.BUY, quantity=1.0, price=100.0, fee=1.0)
    closing = _trade(side=Side.SELL, quantity=1.0, price=110.0, fee=1.0)

    portfolio.apply_fill(*opening)
    portfolio.apply_fill(*closing)

    snapshot = portfolio.snapshot()
    assert snapshot["total_realized_pnl"] == 10.0
    assert snapshot["total_fees"] == 2.0
    assert snapshot["realized_pnl_high_watermark"] == 8.0
    assert snapshot["realized_drawdown"] == 0.0

    next_open = _trade(side=Side.BUY, quantity=1.0, price=100.0, fee=1.0)
    next_close = _trade(side=Side.SELL, quantity=1.0, price=95.0, fee=1.0)
    portfolio.apply_fill(*next_open)
    portfolio.apply_fill(*next_close)

    snapshot = portfolio.snapshot()
    assert snapshot["realized_pnl_high_watermark"] == 8.0
    assert snapshot["realized_drawdown"] == 7.0


def test_replaying_same_fills_reconstructs_drawdown_state() -> None:
    trades = [
        _trade(side=Side.BUY, quantity=1.0, price=100.0),
        _trade(side=Side.SELL, quantity=1.0, price=130.0),
        _trade(side=Side.BUY, quantity=1.0, price=100.0),
        _trade(side=Side.SELL, quantity=1.0, price=80.0),
    ]
    first = PaperPortfolio()
    recovered = PaperPortfolio()

    for order, fill in trades:
        first.apply_fill(order, fill)
        recovered.apply_fill(order, fill)

    first_snapshot = first.snapshot()
    recovered_snapshot = recovered.snapshot()
    assert recovered_snapshot["realized_pnl_high_watermark"] == first_snapshot[
        "realized_pnl_high_watermark"
    ]
    assert recovered_snapshot["realized_drawdown"] == first_snapshot["realized_drawdown"]


def test_reset_clears_drawdown_high_watermark() -> None:
    portfolio = PaperPortfolio()
    opening = _trade(side=Side.BUY, quantity=1.0, price=100.0)
    closing = _trade(side=Side.SELL, quantity=1.0, price=120.0)
    portfolio.apply_fill(*opening)
    portfolio.apply_fill(*closing)

    portfolio.reset()

    snapshot = portfolio.snapshot()
    assert snapshot["realized_pnl_high_watermark"] == 0.0
    assert snapshot["realized_drawdown"] == 0.0
