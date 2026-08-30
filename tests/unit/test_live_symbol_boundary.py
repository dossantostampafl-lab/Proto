from datetime import UTC, datetime

import pytest

from services.market_data.core import MarketTick
from services.market_data.live_status import evaluate_live_coverage


def _tick(symbol: str, now: datetime) -> MarketTick:
    return MarketTick(
        timestamp=now,
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


def test_live_coverage_canonicalizes_and_deduplicates_expected_symbols() -> None:
    now = datetime(2026, 8, 30, 12, 0, tzinfo=UTC)
    tick = _tick("BTC", now)

    coverage = evaluate_live_coverage(
        expected_symbols=(" btc ", "BTC", "btc"),
        latest={"BTC": tick},
        symbol_connection_generation={"BTC": 3},
        current_generation=3,
        connected=True,
        stale_after_seconds=10.0,
        received_times={"BTC": now},
        now=now,
    )

    assert coverage["complete"] is True
    assert coverage["fresh_symbols"] == ["BTC"]
    assert coverage["current_connection_symbols"] == ["BTC"]
    assert list(coverage["symbol_health"]) == ["BTC"]


def test_live_coverage_rejects_blank_expected_symbol() -> None:
    with pytest.raises(ValueError, match="blank symbols"):
        evaluate_live_coverage(
            expected_symbols=("BTC", "   "),
            latest={},
            symbol_connection_generation={},
            current_generation=0,
            connected=False,
            stale_after_seconds=10.0,
        )
