from pydantic import ValidationError

from services.market_data.models import OrderBookSnapshot
from services.market_data.replay import ReplayBatch, select_replay_window, summarize_replay


def _replay_payload() -> dict[str, object]:
    return {
        "events": [
            {
                "sequence": 0,
                "event_type": "ORDER_BOOK",
                "observed_at": "2030-01-01T00:00:00Z",
                "data": {
                    "market_id": "btc-usd-replay",
                    "asset": "BTC",
                    "bids": [
                        {"price": 60_000, "size": 1.5},
                        {"price": 59_990, "size": 2.0},
                    ],
                    "asks": [
                        {"price": 60_010, "size": 1.0},
                        {"price": 60_020, "size": 2.5},
                    ],
                    "observed_at": "2030-01-01T00:00:00Z",
                    "source": "HISTORICAL_FIXTURE",
                },
            },
            {
                "sequence": 2,
                "event_type": "CANDLE",
                "observed_at": "2030-01-01T00:01:00Z",
                "data": {
                    "market_id": "btc-usd-replay",
                    "asset": "BTC",
                    "timeframe": "1m",
                    "started_at": "2030-01-01T00:00:00Z",
                    "ended_at": "2030-01-01T00:01:00Z",
                    "open": 60_000,
                    "high": 60_100,
                    "low": 59_950,
                    "close": 60_050,
                    "volume": 25.5,
                    "source": "HISTORICAL_FIXTURE",
                },
            },
        ]
    }


def test_order_book_requires_sorted_non_crossed_levels() -> None:
    valid = OrderBookSnapshot.model_validate(
        {
            "market_id": "btc-usd-replay",
            "asset": "BTC",
            "bids": [{"price": 100, "size": 1}, {"price": 99, "size": 1}],
            "asks": [{"price": 101, "size": 1}, {"price": 102, "size": 1}],
        }
    )
    assert valid.bids[0].price == 100
    assert valid.asks[0].price == 101

    try:
        OrderBookSnapshot.model_validate(
            {
                "market_id": "btc-usd-replay",
                "asset": "BTC",
                "bids": [{"price": 99, "size": 1}, {"price": 100, "size": 1}],
                "asks": [{"price": 101, "size": 1}],
            }
        )
    except ValidationError as error:
        assert "bids must be sorted" in str(error)
    else:
        raise AssertionError("unsorted bids must be rejected")


def test_replay_summary_detects_event_counts_and_sequence_gaps() -> None:
    batch = ReplayBatch.model_validate(_replay_payload())
    summary = summarize_replay(batch)

    assert summary.count == 2
    assert summary.first_sequence == 0
    assert summary.last_sequence == 2
    assert summary.event_counts == {"CANDLE": 1, "ORDER_BOOK": 1}
    assert summary.sequence_gaps == [1]


def test_replay_window_is_deterministic() -> None:
    batch = ReplayBatch.model_validate(_replay_payload())
    selected = select_replay_window(batch, after_sequence=0, limit=1)

    assert len(selected) == 1
    assert selected[0].sequence == 2
    assert selected[0].event_type == "CANDLE"
