from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from apps.api.app.models import Asset, Fill, Side, SimulationOrder
from apps.api.app.portfolio import PaperPortfolio


def _fill(
    portfolio: PaperPortfolio,
    *,
    side: Side,
    quantity: float,
    price: float,
    filled_at: datetime,
) -> None:
    order_id = uuid4()
    order = SimulationOrder(
        id=order_id,
        market_id="btc-paper",
        asset=Asset.BTC,
        side=side,
        quantity=quantity,
        limit_price=price,
        created_at=filled_at,
    )
    fill = Fill(
        order_id=order_id,
        market_id=order.market_id,
        asset=order.asset,
        side=side,
        filled_quantity=quantity,
        fill_price=price,
        fee=0.0,
        slippage_bps=0.0,
        filled_at=filled_at,
    )
    assert portfolio.apply_fill(order, fill) is True


def test_snapshot_reports_position_age_and_temporal_notional_exposure() -> None:
    portfolio = PaperPortfolio()
    opened_at = datetime(2026, 1, 1, tzinfo=UTC)
    _fill(
        portfolio,
        side=Side.BUY,
        quantity=2.0,
        price=100.0,
        filled_at=opened_at,
    )

    snapshot = portfolio.snapshot(as_of=opened_at + timedelta(seconds=10))
    position = snapshot["positions"][0]

    assert position["opened_at"] == opened_at.isoformat()
    assert position["position_age_seconds"] == 10.0
    assert position["temporal_exposure_notional_seconds"] == 2_000.0
    assert snapshot["temporal_exposure_notional_seconds"] == 2_000.0
    assert snapshot["max_position_age_seconds"] == 10.0


def test_temporal_exposure_accrues_across_size_changes() -> None:
    portfolio = PaperPortfolio()
    started = datetime(2026, 1, 1, tzinfo=UTC)
    _fill(portfolio, side=Side.BUY, quantity=2.0, price=100.0, filled_at=started)
    _fill(
        portfolio,
        side=Side.BUY,
        quantity=1.0,
        price=200.0,
        filled_at=started + timedelta(seconds=5),
    )

    snapshot = portfolio.snapshot(as_of=started + timedelta(seconds=10))

    # First five seconds: 2 * 100 * 5 = 1000.
    # Next five seconds: 3 * (400 / 3) * 5 = 2000.
    assert snapshot["temporal_exposure_notional_seconds"] == 3_000.0
    assert snapshot["positions"][0]["position_age_seconds"] == 10.0


def test_position_clock_resets_only_after_flat_or_direction_flip() -> None:
    portfolio = PaperPortfolio()
    started = datetime(2026, 1, 1, tzinfo=UTC)
    _fill(portfolio, side=Side.BUY, quantity=2.0, price=100.0, filled_at=started)
    _fill(
        portfolio,
        side=Side.SELL,
        quantity=1.0,
        price=110.0,
        filled_at=started + timedelta(seconds=5),
    )
    partial = portfolio.snapshot(as_of=started + timedelta(seconds=6))["positions"][0]
    assert partial["opened_at"] == started.isoformat()
    assert partial["position_age_seconds"] == 6.0

    flipped_at = started + timedelta(seconds=10)
    _fill(
        portfolio,
        side=Side.SELL,
        quantity=2.0,
        price=120.0,
        filled_at=flipped_at,
    )
    flipped = portfolio.snapshot(as_of=flipped_at + timedelta(seconds=4))["positions"][0]
    assert flipped["opened_at"] == flipped_at.isoformat()
    assert flipped["position_age_seconds"] == 4.0


def test_out_of_order_fill_cannot_move_temporal_clock_backwards() -> None:
    portfolio = PaperPortfolio()
    started = datetime(2026, 1, 1, tzinfo=UTC)
    _fill(
        portfolio,
        side=Side.BUY,
        quantity=1.0,
        price=100.0,
        filled_at=started + timedelta(seconds=5),
    )

    with pytest.raises(ValueError, match="cannot move position clock backwards"):
        _fill(
            portfolio,
            side=Side.BUY,
            quantity=1.0,
            price=100.0,
            filled_at=started,
        )
