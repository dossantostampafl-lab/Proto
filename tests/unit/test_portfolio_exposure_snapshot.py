from datetime import UTC, datetime

import pytest

from apps.api.app.models import Asset, Fill, Side, SimulationOrder
from apps.api.app.portfolio import PaperPortfolio


def _apply(
    portfolio: PaperPortfolio,
    *,
    asset: Asset,
    side: Side,
    quantity: float,
    price: float,
) -> None:
    now = datetime(2026, 8, 31, 12, 0, tzinfo=UTC)
    order = SimulationOrder(
        market_id=f"{asset.value.lower()}-paper",
        asset=asset,
        side=side,
        quantity=quantity,
        limit_price=price,
        created_at=now,
    )
    fill = Fill(
        order_id=order.id,
        market_id=order.market_id,
        asset=asset,
        side=side,
        filled_quantity=quantity,
        fill_price=price,
        fee=0.0,
        slippage_bps=0.0,
        filled_at=now,
    )
    assert portfolio.apply_fill(order, fill) is True


def test_snapshot_exposes_gross_net_and_concentration_with_marks() -> None:
    portfolio = PaperPortfolio()
    _apply(portfolio, asset=Asset.BTC, side=Side.BUY, quantity=1.0, price=100.0)
    _apply(portfolio, asset=Asset.ETH, side=Side.SELL, quantity=2.0, price=50.0)

    snapshot = portfolio.snapshot({Asset.BTC: 120.0, Asset.ETH: 40.0})

    assert snapshot["open_position_count"] == 2
    assert snapshot["gross_exposure"] == 200.0
    assert snapshot["net_exposure"] == 40.0
    assert snapshot["max_asset_concentration"] == pytest.approx(0.6)
    assert snapshot["exposure_by_asset"] == {"BTC": 120.0, "ETH": 80.0}


def test_snapshot_falls_back_to_average_price_without_marks() -> None:
    portfolio = PaperPortfolio()
    _apply(portfolio, asset=Asset.SOL, side=Side.BUY, quantity=3.0, price=25.0)

    snapshot = portfolio.snapshot()

    assert snapshot["gross_exposure"] == 75.0
    assert snapshot["net_exposure"] == 75.0
    assert snapshot["max_asset_concentration"] == 1.0
    assert snapshot["exposure_by_asset"] == {"SOL": 75.0}


def test_closed_positions_do_not_count_as_open_exposure() -> None:
    portfolio = PaperPortfolio()
    _apply(portfolio, asset=Asset.BTC, side=Side.BUY, quantity=1.0, price=100.0)
    _apply(portfolio, asset=Asset.BTC, side=Side.SELL, quantity=1.0, price=110.0)

    snapshot = portfolio.snapshot({Asset.BTC: 120.0})

    assert snapshot["open_position_count"] == 0
    assert snapshot["gross_exposure"] == 0.0
    assert snapshot["net_exposure"] == 0.0
    assert snapshot["max_asset_concentration"] == 0.0
    assert snapshot["exposure_by_asset"] == {}
