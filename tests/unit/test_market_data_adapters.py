from datetime import UTC, datetime

import pytest

from services.market_data.adapters import (
    CSVReplayAdapter,
    HistoricalReplayAdapter,
    SyntheticAdapter,
)
from services.market_data.core import MarketTick
from services.market_data.live import CoinbasePublicMarketDataAdapter
from services.market_data.public_feed_parser import (
    PublicCryptoFeedError,
    parse_public_ticker_message,
)


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


def _public_payload(*, timestamp: str = "2026-08-29T20:15:00Z") -> dict[str, object]:
    return {
        "channel": "ticker",
        "timestamp": timestamp,
        "sequence_num": 42,
        "events": [
            {
                "tickers": [
                    {
                        "product_id": "BTC-USD",
                        "price": "61000.25",
                        "best_bid": "61000.00",
                        "best_ask": "61000.50",
                        "best_bid_quantity": "1.2",
                        "best_ask_quantity": "0.8",
                        "volume_24_h": "123.4",
                    }
                ]
            }
        ],
    }


def test_public_ticker_parser_normalizes_supported_crypto() -> None:
    ticks = parse_public_ticker_message(_public_payload())

    assert len(ticks) == 1
    tick = ticks[0]
    assert tick.venue == "coinbase-public"
    assert tick.symbol == "BTC"
    assert tick.sequence == 42
    assert tick.timestamp.tzinfo == UTC
    assert tick.mid == pytest.approx(61000.25)
    assert tick.bid_size == pytest.approx(1.2)
    assert tick.ask_size == pytest.approx(0.8)


def test_public_ticker_parser_normalizes_offset_timestamp_to_utc() -> None:
    ticks = parse_public_ticker_message(
        _public_payload(timestamp="2026-08-29T17:15:00-03:00")
    )

    assert ticks[0].timestamp == datetime(2026, 8, 29, 20, 15, tzinfo=UTC)


def test_public_ticker_parser_ignores_non_ticker_channels() -> None:
    assert parse_public_ticker_message({"channel": "heartbeats"}) == []


def test_public_ticker_parser_rejects_malformed_frames() -> None:
    with pytest.raises(PublicCryptoFeedError):
        parse_public_ticker_message(
            {
                "channel": "ticker",
                "timestamp": "2026-08-29T20:15:00Z",
                "sequence_num": 1,
                "events": "not-an-array",
            }
        )


def test_public_ticker_parser_rejects_invalid_json_as_feed_error() -> None:
    with pytest.raises(PublicCryptoFeedError, match="invalid public feed payload"):
        parse_public_ticker_message("{not-json")


def test_public_ticker_parser_rejects_timezone_naive_timestamp() -> None:
    with pytest.raises(PublicCryptoFeedError, match="timezone-aware"):
        parse_public_ticker_message(_public_payload(timestamp="2026-08-29T20:15:00"))


def test_public_ticker_parser_rejects_invalid_sequence() -> None:
    payload = _public_payload()
    payload["sequence_num"] = "not-a-number"

    with pytest.raises(PublicCryptoFeedError, match="ticker sequence is invalid"):
        parse_public_ticker_message(payload)


def test_public_adapter_accepts_only_read_only_crypto_products() -> None:
    adapter = CoinbasePublicMarketDataAdapter(products=("BTC-USD", "ETH-USD", "SOL-USD"))
    assert adapter.products == ("BTC-USD", "ETH-USD", "SOL-USD")

    with pytest.raises(ValueError):
        CoinbasePublicMarketDataAdapter(products=("DOGE-USD",))
