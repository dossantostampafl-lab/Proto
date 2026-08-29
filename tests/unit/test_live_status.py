from datetime import UTC, datetime, timedelta

from services.market_data.core import MarketTick
from services.market_data.live_status import (
    evaluate_live_coverage,
    live_readiness_failures,
)


def _tick(symbol: str, observed_at: datetime) -> MarketTick:
    return MarketTick(
        timestamp=observed_at,
        venue="coinbase-public",
        symbol=symbol,
        bid=100.0,
        ask=101.0,
        last=100.5,
        volume=1.0,
        bid_size=1.0,
        ask_size=1.0,
        sequence=1,
    )


def test_live_coverage_reports_missing_and_stale_symbols() -> None:
    now = datetime(2026, 8, 29, 21, 0, tzinfo=UTC)
    coverage = evaluate_live_coverage(
        expected_symbols=("BTC", "ETH", "SOL"),
        latest={
            "BTC": _tick("BTC", now - timedelta(seconds=1)),
            "ETH": _tick("ETH", now - timedelta(seconds=20)),
        },
        symbol_connection_generation={"BTC": 2, "ETH": 2},
        current_generation=2,
        connected=True,
        stale_after_seconds=10.0,
        now=now,
    )

    assert coverage["receiving_data"] is True
    assert coverage["complete"] is False
    assert coverage["all_symbols_fresh"] is False
    assert coverage["all_symbols_current_connection"] is False
    assert coverage["fresh_symbols"] == ["BTC"]
    assert coverage["stale_symbols"] == ["ETH"]
    assert coverage["missing_symbols"] == ["SOL"]
    assert coverage["current_connection_symbols"] == ["BTC", "ETH"]


def test_live_coverage_requires_current_connection_generation() -> None:
    now = datetime(2026, 8, 29, 21, 0, tzinfo=UTC)
    latest = {
        symbol: _tick(symbol, now - timedelta(seconds=1))
        for symbol in ("BTC", "ETH", "SOL")
    }

    coverage = evaluate_live_coverage(
        expected_symbols=("BTC", "ETH", "SOL"),
        latest=latest,
        symbol_connection_generation={"BTC": 1, "ETH": 1, "SOL": 1},
        current_generation=2,
        connected=True,
        stale_after_seconds=10.0,
        now=now,
    )

    assert coverage["complete"] is True
    assert coverage["all_symbols_fresh"] is True
    assert coverage["all_symbols_current_connection"] is False
    assert coverage["current_connection_symbols"] == []


def test_live_coverage_is_fully_healthy_for_fresh_current_generation() -> None:
    now = datetime(2026, 8, 29, 21, 0, tzinfo=UTC)
    latest = {
        symbol: _tick(symbol, now - timedelta(seconds=1))
        for symbol in ("BTC", "ETH", "SOL")
    }

    coverage = evaluate_live_coverage(
        expected_symbols=("BTC", "ETH", "SOL"),
        latest=latest,
        symbol_connection_generation={"BTC": 3, "ETH": 3, "SOL": 3},
        current_generation=3,
        connected=True,
        stale_after_seconds=10.0,
        now=now,
    )

    assert coverage["complete"] is True
    assert coverage["all_symbols_fresh"] is True
    assert coverage["all_symbols_current_connection"] is True
    assert coverage["stale"] is False
    assert coverage["missing_symbols"] == []


def test_readiness_failures_are_explicit_for_stopped_disconnected_monitor() -> None:
    failures = live_readiness_failures(
        running=False,
        connected=False,
        message_fresh=False,
        coverage={
            "receiving_data": False,
            "complete": False,
            "all_symbols_fresh": False,
            "all_symbols_current_connection": False,
        },
    )

    assert failures == [
        "MONITOR_STOPPED",
        "SOURCE_DISCONNECTED",
        "NO_FRESH_DATA",
        "INCOMPLETE_SYMBOL_COVERAGE",
    ]


def test_readiness_failures_are_empty_for_healthy_live_coverage() -> None:
    failures = live_readiness_failures(
        running=True,
        connected=True,
        message_fresh=True,
        coverage={
            "receiving_data": True,
            "complete": True,
            "all_symbols_fresh": True,
            "all_symbols_current_connection": True,
            "feed_health": {"consecutive_parse_errors": 0},
        },
    )

    assert failures == []


def test_readiness_fails_closed_while_public_parser_is_degraded() -> None:
    failures = live_readiness_failures(
        running=True,
        connected=True,
        message_fresh=True,
        coverage={
            "receiving_data": True,
            "complete": True,
            "all_symbols_fresh": True,
            "all_symbols_current_connection": True,
            "feed_health": {"consecutive_parse_errors": 1},
        },
    )

    assert failures == ["SOURCE_PARSE_DEGRADED"]
