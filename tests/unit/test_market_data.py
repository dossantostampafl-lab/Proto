from datetime import UTC, datetime, timedelta

from services.market_data.core import (
    DataQualityIssue,
    DataQualityMonitor,
    MarketTick,
    compute_orderbook_metrics,
)


def _tick(**overrides: object) -> MarketTick:
    payload: dict[str, object] = {
        "timestamp": datetime(2026, 8, 29, 0, 0, tzinfo=UTC),
        "venue": "synthetic",
        "symbol": "BTC",
        "bid": 60_000.0,
        "ask": 60_010.0,
        "last": 60_005.0,
        "volume": 12.0,
        "bid_size": 3.0,
        "ask_size": 1.0,
        "sequence": 10,
    }
    payload.update(overrides)
    return MarketTick(**payload)


def test_orderbook_metrics_use_size_weighted_microprice() -> None:
    metrics = compute_orderbook_metrics(_tick())

    assert metrics.mid_price == 60_005.0
    assert metrics.spread == 10.0
    assert metrics.depth == 4.0
    assert metrics.imbalance == 0.5
    assert metrics.microprice == 60_007.5


def test_quality_monitor_rejects_duplicate_sequence() -> None:
    monitor = DataQualityMonitor(stale_after_seconds=30)
    now = datetime(2026, 8, 29, 0, 0, 1, tzinfo=UTC)

    first = monitor.evaluate(_tick(), now=now)
    duplicate = monitor.evaluate(_tick(), now=now)

    assert first.valid is True
    assert duplicate.valid is False
    assert DataQualityIssue.DUPLICATE_SEQUENCE in duplicate.issues


def test_quality_monitor_detects_stale_invalid_and_negative_data() -> None:
    monitor = DataQualityMonitor(stale_after_seconds=5)
    tick = _tick(
        timestamp=datetime(2026, 8, 29, 0, 0, tzinfo=UTC),
        bid=101.0,
        ask=100.0,
        bid_size=-1.0,
        volume=-2.0,
    )

    report = monitor.evaluate(
        tick,
        now=datetime(2026, 8, 29, 0, 0, 10, tzinfo=UTC),
    )

    assert report.valid is False
    assert DataQualityIssue.STALE_FEED in report.issues
    assert DataQualityIssue.INVALID_SPREAD in report.issues
    assert DataQualityIssue.NEGATIVE_SIZE in report.issues
    assert DataQualityIssue.NEGATIVE_VOLUME in report.issues


def test_quality_monitor_detects_out_of_order_and_price_jump() -> None:
    monitor = DataQualityMonitor(
        stale_after_seconds=120,
        max_relative_price_jump=0.05,
    )
    now = datetime(2026, 8, 29, 0, 1, tzinfo=UTC)
    first = _tick()
    monitor.evaluate(first, now=now)

    later = _tick(
        timestamp=first.timestamp - timedelta(seconds=1),
        sequence=9,
        bid=70_000.0,
        ask=70_010.0,
        last=70_005.0,
    )
    report = monitor.evaluate(later, now=now)

    assert DataQualityIssue.OUT_OF_ORDER_SEQUENCE in report.issues
    assert DataQualityIssue.OUT_OF_ORDER_TIMESTAMP in report.issues
    assert DataQualityIssue.PRICE_JUMP in report.issues


def test_quality_monitor_rejects_future_timestamp_beyond_clock_skew() -> None:
    now = datetime(2026, 8, 29, 0, 0, tzinfo=UTC)
    monitor = DataQualityMonitor(
        stale_after_seconds=5,
        max_future_skew_seconds=1.0,
    )

    report = monitor.evaluate(
        _tick(timestamp=now + timedelta(seconds=2)),
        now=now,
    )

    assert report.valid is False
    assert DataQualityIssue.FUTURE_TIMESTAMP in report.issues


def test_quality_monitor_rejects_timezone_naive_timestamp_without_crashing() -> None:
    monitor = DataQualityMonitor(stale_after_seconds=5)

    report = monitor.evaluate(
        _tick(timestamp=datetime(2026, 8, 29, 0, 0)),
        now=datetime(2026, 8, 29, 0, 0, tzinfo=UTC),
    )

    assert report.valid is False
    assert DataQualityIssue.NAIVE_TIMESTAMP in report.issues


def test_quality_monitor_rejects_non_finite_market_values() -> None:
    monitor = DataQualityMonitor(stale_after_seconds=5)
    now = datetime(2026, 8, 29, 0, 0, tzinfo=UTC)

    for field, value in (("bid", float("nan")), ("volume", float("inf"))):
        report = monitor.evaluate(_tick(**{field: value}), now=now)

        assert report.valid is False
        assert DataQualityIssue.NON_FINITE_VALUE in report.issues
