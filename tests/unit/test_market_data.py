from datetime import UTC, datetime, timedelta

from services.market_data.core import (
    DataQualityIssue,
    DataQualityMonitor,
    MarketTick,
    compute_orderbook_metrics,
)


def _tick(
    *,
    sequence: int = 1,
    timestamp: datetime | None = None,
    bid: float = 100.0,
    ask: float = 101.0,
    last: float = 100.5,
    bid_size: float = 2.0,
    ask_size: float = 1.0,
    volume: float = 4.0,
) -> MarketTick:
    return MarketTick(
        timestamp=timestamp or datetime(2026, 1, 1, tzinfo=UTC),
        venue="synthetic",
        symbol="BTC",
        bid=bid,
        ask=ask,
        last=last,
        volume=volume,
        bid_size=bid_size,
        ask_size=ask_size,
        sequence=sequence,
    )


def test_orderbook_metrics_are_deterministic() -> None:
    metrics = compute_orderbook_metrics(_tick())

    assert metrics.best_bid == 100.0
    assert metrics.best_ask == 101.0
    assert metrics.mid_price == 100.5
    assert metrics.spread == 1.0
    assert metrics.depth == 3.0
    assert round(metrics.imbalance, 6) == round(1 / 3, 6)
    assert round(metrics.microprice, 6) == round((101 * 2 + 100 * 1) / 3, 6)


def test_data_quality_accepts_valid_monotonic_ticks() -> None:
    monitor = DataQualityMonitor(stale_after_seconds=5.0)
    base = datetime(2026, 1, 1, tzinfo=UTC)

    first = monitor.evaluate(_tick(sequence=1, timestamp=base), now=base)
    second = monitor.evaluate(
        _tick(sequence=2, timestamp=base + timedelta(seconds=1)),
        now=base + timedelta(seconds=1),
    )

    assert first.valid is True
    assert second.valid is True
    assert first.issues == []
    assert second.issues == []


def test_data_quality_rejects_duplicate_and_out_of_order_sequence() -> None:
    monitor = DataQualityMonitor(stale_after_seconds=5.0)
    base = datetime(2026, 1, 1, tzinfo=UTC)
    monitor.evaluate(_tick(sequence=10, timestamp=base), now=base)

    duplicate = monitor.evaluate(
        _tick(sequence=10, timestamp=base + timedelta(seconds=1)),
        now=base + timedelta(seconds=1),
    )
    out_of_order = monitor.evaluate(
        _tick(sequence=9, timestamp=base + timedelta(seconds=2)),
        now=base + timedelta(seconds=2),
    )

    assert DataQualityIssue.DUPLICATE_SEQUENCE in duplicate.issues
    assert DataQualityIssue.OUT_OF_ORDER_SEQUENCE in out_of_order.issues


def test_data_quality_rejects_timestamp_regression_and_stale_feed() -> None:
    monitor = DataQualityMonitor(stale_after_seconds=2.0)
    base = datetime(2026, 1, 1, tzinfo=UTC)
    monitor.evaluate(_tick(sequence=1, timestamp=base), now=base)

    regressed = monitor.evaluate(
        _tick(sequence=2, timestamp=base - timedelta(milliseconds=1)),
        now=base,
    )
    stale = monitor.evaluate(
        _tick(sequence=2, timestamp=base + timedelta(seconds=1)),
        now=base + timedelta(seconds=4),
    )

    assert DataQualityIssue.OUT_OF_ORDER_TIMESTAMP in regressed.issues
    assert DataQualityIssue.STALE_FEED in stale.issues


def test_data_quality_rejects_price_jump_invalid_spread_and_negative_size() -> None:
    monitor = DataQualityMonitor(max_relative_price_jump=0.05)
    base = datetime(2026, 1, 1, tzinfo=UTC)
    monitor.evaluate(_tick(sequence=1, timestamp=base), now=base)

    jump = monitor.evaluate(
        _tick(
            sequence=2,
            timestamp=base + timedelta(seconds=1),
            bid=120.0,
            ask=121.0,
            last=120.5,
        ),
        now=base + timedelta(seconds=1),
    )
    malformed = monitor.evaluate(
        _tick(
            sequence=2,
            timestamp=base + timedelta(seconds=1),
            bid=102.0,
            ask=101.0,
            bid_size=-1.0,
        ),
        now=base + timedelta(seconds=1),
    )

    assert DataQualityIssue.PRICE_JUMP in jump.issues
    assert DataQualityIssue.INVALID_SPREAD in malformed.issues
    assert DataQualityIssue.NEGATIVE_SIZE in malformed.issues


def test_data_quality_reset_recovers_state_after_fault_injection() -> None:
    monitor = DataQualityMonitor()
    base = datetime(2026, 1, 1, tzinfo=UTC)
    monitor.evaluate(_tick(sequence=5, timestamp=base), now=base)
    rejected = monitor.evaluate(_tick(sequence=4, timestamp=base), now=base)
    assert rejected.valid is False

    monitor.reset()
    recovered = monitor.evaluate(_tick(sequence=1, timestamp=base), now=base)

    assert recovered.valid is True
    assert recovered.issues == []
