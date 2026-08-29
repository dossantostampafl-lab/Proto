import pytest

from services.market_data.public_feed_parser import (
    PublicCryptoFeedError,
    parse_public_ticker_message,
)


def _payload(sequence: object = 42) -> dict[str, object]:
    return {
        "channel": "ticker",
        "timestamp": "2026-08-29T20:15:00Z",
        "sequence_num": sequence,
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


def test_public_ticker_requires_sequence_number() -> None:
    payload = _payload()
    payload.pop("sequence_num")

    with pytest.raises(PublicCryptoFeedError, match="ticker sequence is invalid"):
        parse_public_ticker_message(payload)


@pytest.mark.parametrize("sequence", [True, False, 1.5, None])
def test_public_ticker_rejects_ambiguous_sequence_types(sequence: object) -> None:
    with pytest.raises(PublicCryptoFeedError, match="ticker sequence is invalid"):
        parse_public_ticker_message(_payload(sequence))


def test_public_ticker_accepts_decimal_sequence_string() -> None:
    ticks = parse_public_ticker_message(_payload("42"))

    assert len(ticks) == 1
    assert ticks[0].sequence == 42
