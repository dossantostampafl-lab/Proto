from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from services.portfolio.accounting import (
    FillRecord,
    FillSide,
    PositionLedger,
    reconcile_position,
)


def _fill(
    fill_id: str,
    *,
    side: FillSide,
    quantity: str,
    price: str,
    fee: str = "0",
    offset_seconds: int = 0,
) -> FillRecord:
    return FillRecord(
        fill_id=fill_id,
        symbol="BTC",
        side=side,
        quantity=Decimal(quantity),
        price=Decimal(price),
        fee=Decimal(fee),
        observed_at=datetime(2026, 8, 31, 17, 0, tzinfo=UTC)
        + timedelta(seconds=offset_seconds),
    )


def test_long_position_realizes_pnl_and_tracks_fees_exactly() -> None:
    ledger = PositionLedger("btc")
    ledger.apply_fill(_fill("b1", side=FillSide.BUY, quantity="2", price="100", fee="0.10"))
    ledger.apply_fill(
        _fill(
            "s1",
            side=FillSide.SELL,
            quantity="1",
            price="110",
            fee="0.20",
            offset_seconds=1,
        )
    )

    snapshot = ledger.snapshot(mark_price=Decimal("105"))

    assert snapshot.quantity == Decimal("1")
    assert snapshot.average_price == Decimal("100")
    assert snapshot.realized_pnl == Decimal("10")
    assert snapshot.fees == Decimal("0.30")
    assert snapshot.net_realized_pnl == Decimal("9.70")
    assert snapshot.unrealized_pnl == Decimal("5")


def test_short_cover_realizes_positive_pnl_when_price_falls() -> None:
    ledger = PositionLedger("BTC")
    ledger.apply_fill(_fill("s1", side=FillSide.SELL, quantity="2", price="100"))
    ledger.apply_fill(
        _fill(
            "b1",
            side=FillSide.BUY,
            quantity="1",
            price="90",
            offset_seconds=1,
        )
    )

    snapshot = ledger.snapshot(mark_price=Decimal("95"))

    assert snapshot.quantity == Decimal("-1")
    assert snapshot.realized_pnl == Decimal("10")
    assert snapshot.unrealized_pnl == Decimal("5")


def test_fill_can_close_and_flip_position_with_new_basis() -> None:
    ledger = PositionLedger("BTC")
    ledger.apply_fill(_fill("b1", side=FillSide.BUY, quantity="1", price="100"))
    ledger.apply_fill(
        _fill(
            "s1",
            side=FillSide.SELL,
            quantity="3",
            price="110",
            offset_seconds=1,
        )
    )

    snapshot = ledger.snapshot()

    assert snapshot.quantity == Decimal("-2")
    assert snapshot.average_price == Decimal("110")
    assert snapshot.realized_pnl == Decimal("10")


def test_reconciliation_detects_accounting_mismatch() -> None:
    fills = (
        _fill("b1", side=FillSide.BUY, quantity="2", price="100", fee="0.10"),
        _fill(
            "s1",
            side=FillSide.SELL,
            quantity="1",
            price="110",
            fee="0.20",
            offset_seconds=1,
        ),
    )
    actual = PositionLedger("BTC")
    for fill in fills:
        actual.apply_fill(fill)
    snapshot = actual.snapshot(mark_price=Decimal("105")).model_copy(
        update={"quantity": Decimal("2")}
    )

    result = reconcile_position(fills, actual=snapshot)

    assert result.matched is False
    assert result.mismatches == ("quantity",)


def test_duplicate_fill_is_rejected_idempotently() -> None:
    ledger = PositionLedger("BTC")
    fill = _fill("same", side=FillSide.BUY, quantity="1", price="100")
    ledger.apply_fill(fill)

    with pytest.raises(ValueError, match="duplicate fill_id"):
        ledger.apply_fill(fill)
