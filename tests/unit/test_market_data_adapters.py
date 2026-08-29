from datetime import UTC, datetime

import pytest

from services.market_data.adapters import (
    CSVReplayAdapter,
    HistoricalReplayAdapter,
    PublicReadOnlyHTTPAdapter,
    SyntheticAdapter,
)
from services.market_data.core import MarketTick


def test_synthetic_adapter_is_deterministic() -> None:
    first = list(
        SyntheticAdapter(
            symbol="BTC",
            start_price=60_000,
            seed=11,
            count=3,
        ).stream()
    )
    second = list(
        SyntheticAdapter(
            symbol="BTC",
            start_price=60_000,
            seed=11,
            count=3,
        ).stream()
    )

    assert first == second
    assert [tick.sequence for tick in first] == [0, 1, 2]
    assert all(tick.bid < tick.ask for tick in first)


def test_csv_replay_adapter_parses_contract() -> None:
    csv_text = (
        "timestamp,venue,symbol,bid,ask,last,volume,bid_size,ask_size,sequence\n"
        "2026-08-29T00:00:00+00:00,replay,ETH,3000,3001,3000.5,4,2,3,7\n"
    )

    ticks = list(CSVReplayAdapter(csv_text).stream())

    assert len(ticks) == 1
    assert ticks[0].symbol == "ETH"
    assert ticks[0].sequence == 7
    assert ticks[0].mid == 3000.5


def test_historical_adapter_orders_timestamp_then_sequence() -> None:
    base = datetime(2026, 8, 29, tzinfo=UTC)
    late = MarketTick(
        timestamp=base,
        venue="replay",
        symbol="SOL",
        bid=140,
        ask=141,
        last=140.5,
        volume=1,
        bid_size=1,
        ask_size=1,
        sequence=2,
    )
    early = late.model_copy(update={"sequence": 1})

    ticks = list(HistoricalReplayAdapter([late, early]).stream())

    assert [tick.sequence for tick in ticks] == [1, 2]


def _parse_public_tick(payload: dict[str, object], sequence: int) -> MarketTick:
    return MarketTick(
        timestamp=datetime(2026, 8, 29, tzinfo=UTC),
        venue="public-read-only",
        symbol=str(payload["symbol"]),
        bid=float(payload["bid"]),
        ask=float(payload["ask"]),
        last=float(payload["last"]),
        volume=float(payload["volume"]),
        bid_size=float(payload["bid_size"]),
        ask_size=float(payload["ask_size"]),
        sequence=sequence,
    )


def test_public_read_only_adapter_normalizes_injected_public_get() -> None:
    calls: list[str] = []

    def fetch_json(url: str) -> dict[str, object]:
        calls.append(url)
        return {
            "symbol": "BTC",
            "bid": 60_000,
            "ask": 60_010,
            "last": 60_005,
            "volume": 10,
            "bid_size": 2,
            "ask_size": 3,
        }

    adapter = PublicReadOnlyHTTPAdapter(
        url="https://data.example.test/ticker",
        allowed_hosts=frozenset({"data.example.test"}),
        fetch_json=fetch_json,
        parse_tick=_parse_public_tick,
        count=2,
    )

    ticks = list(adapter.stream())

    assert adapter.read_only is True
    assert adapter.requires_trading_credentials is False
    assert calls == ["https://data.example.test/ticker"] * 2
    assert [tick.sequence for tick in ticks] == [0, 1]
    assert not hasattr(adapter, "place_order")
    assert not hasattr(adapter, "submit_order")


@pytest.mark.parametrize(
    "url",
    [
        "http://data.example.test/ticker",
        "https://user:secret@data.example.test/ticker",
        "https://not-allowed.example.test/ticker",
    ],
)
def test_public_read_only_adapter_rejects_unsafe_endpoints(url: str) -> None:
    with pytest.raises(ValueError):
        PublicReadOnlyHTTPAdapter(
            url=url,
            allowed_hosts=frozenset({"data.example.test"}),
            fetch_json=lambda _: {},
            parse_tick=_parse_public_tick,
        )
