from datetime import UTC, datetime, timedelta, timezone

import pytest
from pydantic import ValidationError

from services.market_data.core import MarketTick


def _payload(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "timestamp": datetime(2026, 8, 30, 12, 0, tzinfo=UTC),
        "venue": "coinbase-public",
        "symbol": "BTC",
        "bid": 60_000.0,
        "ask": 60_001.0,
        "last": 60_000.5,
        "volume": 120.0,
        "bid_size": 2.0,
        "ask_size": 1.5,
        "sequence": 1,
    }
    payload.update(overrides)
    return payload


def test_market_tick_normalizes_boundary_identifiers_and_timestamp() -> None:
    source_tz = timezone(timedelta(hours=-3))
    tick = MarketTick(
        **_payload(
            timestamp=datetime(2026, 8, 30, 9, 0, tzinfo=source_tz),
            venue=" coinbase-public ",
            symbol=" btc ",
        )
    )

    assert tick.timestamp == datetime(2026, 8, 30, 12, 0, tzinfo=UTC)
    assert tick.venue == "coinbase-public"
    assert tick.symbol == "BTC"


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("bid", 0.0),
        ("ask", -1.0),
        ("last", float("nan")),
        ("volume", -1.0),
        ("bid_size", -1.0),
        ("ask_size", float("inf")),
    ],
)
def test_market_tick_rejects_invalid_numeric_domain(field: str, value: float) -> None:
    with pytest.raises(ValidationError):
        MarketTick(**_payload(**{field: value}))


def test_market_tick_rejects_crossed_book_at_domain_boundary() -> None:
    with pytest.raises(ValidationError):
        MarketTick(**_payload(bid=101.0, ask=100.0, last=100.5))


def test_market_tick_rejects_naive_timestamp_at_domain_boundary() -> None:
    with pytest.raises(ValidationError):
        MarketTick(**_payload(timestamp=datetime(2026, 8, 30, 12, 0)))


def test_market_tick_remains_immutable_after_validation() -> None:
    tick = MarketTick(**_payload())

    with pytest.raises(ValidationError):
        tick.bid = 1.0
