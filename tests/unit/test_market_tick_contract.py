from datetime import UTC, datetime

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


def test_market_tick_normalizes_boundary_identifiers() -> None:
    tick = MarketTick(
        **_payload(
            venue=" coinbase-public ",
            symbol=" btc ",
        )
    )

    assert tick.venue == "coinbase-public"
    assert tick.symbol == "BTC"


def test_market_tick_rejects_blank_boundary_identifiers() -> None:
    for field in ("venue", "symbol"):
        with pytest.raises(ValidationError):
            MarketTick(**_payload(**{field: "   "}))


def test_market_tick_remains_immutable_after_normalization() -> None:
    tick = MarketTick(**_payload())

    with pytest.raises(ValidationError):
        tick.symbol = "ETH"
