import math
from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError

from services.market_data import (
    BinaryContractSnapshot,
    BookLevel,
    Candle,
    DataSource,
    OrderBookSnapshot,
)


def test_order_book_snapshot_enforces_sorting_and_exposes_metrics() -> None:
    snapshot = OrderBookSnapshot(
        market_id="btc-replay-book",
        asset="BTC",
        bids=(BookLevel(price=100.0, size=2.0), BookLevel(price=99.0, size=1.0)),
        asks=(BookLevel(price=101.0, size=3.0), BookLevel(price=102.0, size=4.0)),
        observed_at=datetime(2026, 8, 31, 12, 0, tzinfo=UTC),
        source=DataSource.HISTORICAL_REPLAY,
    )

    assert snapshot.mid_price == 100.5
    assert snapshot.spread == 1.0
    assert snapshot.total_depth == 10.0


def test_order_book_snapshot_rejects_crossed_or_unsorted_books() -> None:
    now = datetime(2026, 8, 31, 12, 0, tzinfo=UTC)
    with pytest.raises(ValidationError):
        OrderBookSnapshot(
            market_id="btc-crossed",
            asset="BTC",
            bids=(BookLevel(price=102.0, size=1.0),),
            asks=(BookLevel(price=101.0, size=1.0),),
            observed_at=now,
        )

    with pytest.raises(ValidationError):
        OrderBookSnapshot(
            market_id="btc-unsorted",
            asset="BTC",
            bids=(BookLevel(price=99.0, size=1.0), BookLevel(price=100.0, size=1.0)),
            asks=(BookLevel(price=101.0, size=1.0),),
            observed_at=now,
        )


def test_candle_enforces_time_and_ohlc_invariants() -> None:
    started = datetime(2026, 8, 31, 12, 0, tzinfo=UTC)
    candle = Candle(
        market_id="eth-1m",
        asset="ETH",
        timeframe="1m",
        started_at=started,
        ended_at=started + timedelta(minutes=1),
        open=200.0,
        high=210.0,
        low=195.0,
        close=205.0,
        volume=42.0,
    )
    assert candle.source == DataSource.HISTORICAL_REPLAY

    with pytest.raises(ValidationError):
        Candle(
            market_id="eth-invalid",
            asset="ETH",
            timeframe="1m",
            started_at=started,
            ended_at=started + timedelta(minutes=1),
            open=200.0,
            high=202.0,
            low=195.0,
            close=205.0,
            volume=42.0,
        )


def test_binary_contract_exposes_implied_probability_and_spread() -> None:
    observed = datetime(2026, 8, 31, 12, 0, tzinfo=UTC)
    contract = BinaryContractSnapshot(
        market_id="btc-threshold",
        underlying_asset="BTC",
        yes_bid=0.48,
        yes_ask=0.52,
        observed_at=observed,
        expires_at=observed + timedelta(hours=1),
    )

    assert contract.implied_probability == 0.5
    assert contract.probability_spread == pytest.approx(0.04)


def test_contracts_reject_non_finite_or_naive_inputs() -> None:
    with pytest.raises(ValidationError):
        BookLevel(price=math.inf, size=1.0)

    naive = datetime(2026, 8, 31, 12, 0)
    with pytest.raises(ValidationError):
        BinaryContractSnapshot(
            market_id="sol-naive",
            underlying_asset="SOL",
            yes_bid=0.4,
            yes_ask=0.5,
            observed_at=naive,
            expires_at=naive + timedelta(hours=1),
        )
