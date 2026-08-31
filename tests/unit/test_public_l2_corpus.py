from pathlib import Path

import pytest

from services.market_data import DataSource
from services.market_data.public_l2 import (
    PublicL2Book,
    PublicL2IntegrityError,
    parse_public_l2_message,
)
from services.replay import ReplayEngine

FIXTURE_DIR = Path(__file__).resolve().parents[1] / "fixtures" / "public_feeds"


def _message(
    sequence: int,
    *,
    event_type: str = "snapshot",
    updates: list[dict[str, str]] | None = None,
) -> dict[str, object]:
    return {
        "channel": "l2_data",
        "timestamp": f"2026-08-31T20:00:{sequence:02d}Z",
        "sequence_num": sequence,
        "events": [
            {
                "type": event_type,
                "product_id": "BTC-USD",
                "updates": updates
                or [
                    {
                        "side": "bid",
                        "event_time": "2026-08-31T20:00:00Z",
                        "price_level": "61000.00",
                        "new_quantity": "1.2",
                    },
                    {
                        "side": "offer",
                        "event_time": "2026-08-31T20:00:00Z",
                        "price_level": "61000.50",
                        "new_quantity": "0.8",
                    },
                ],
            }
        ],
    }


def test_coinbase_level2_golden_fixture_parses_offline() -> None:
    payload = (FIXTURE_DIR / "coinbase_advanced_level2.json").read_text(
        encoding="utf-8"
    )

    frame = parse_public_l2_message(payload)

    assert frame is not None
    assert frame.sequence == 0
    assert len(frame.events) == 1
    event = frame.events[0]
    assert event.event_type == "snapshot"
    assert event.product_id == "BTC-USD"
    assert event.asset == "BTC"
    assert [update.side for update in event.updates] == ["bid", "bid"]
    assert str(event.updates[0].price) == "21921.73"
    assert str(event.updates[0].quantity) == "0.06317902"


def test_public_l2_book_reconstructs_update_and_zero_quantity_delete() -> None:
    book = PublicL2Book(snapshot_depth=10)
    first = book.ingest(_message(10))[0]

    assert first.source == DataSource.PUBLIC_READ_ONLY
    assert first.asset == "BTC"
    assert first.bids[0].price == 61000.0
    assert first.asks[0].price == 61000.5

    second = book.ingest(
        _message(
            11,
            event_type="update",
            updates=[
                {
                    "side": "bid",
                    "event_time": "2026-08-31T20:00:11Z",
                    "price_level": "61000.00",
                    "new_quantity": "0",
                },
                {
                    "side": "bid",
                    "event_time": "2026-08-31T20:00:11Z",
                    "price_level": "60999.75",
                    "new_quantity": "2.5",
                },
                {
                    "side": "offer",
                    "event_time": "2026-08-31T20:00:11Z",
                    "price_level": "61000.50",
                    "new_quantity": "1.1",
                },
            ],
        )
    )[0]

    assert [level.price for level in second.bids] == [60999.75]
    assert second.bids[0].size == 2.5
    assert second.asks[0].size == 1.1


def test_public_l2_book_fails_closed_on_sequence_gap() -> None:
    book = PublicL2Book()
    book.ingest(_message(20))

    with pytest.raises(PublicL2IntegrityError, match="sequence gap"):
        book.ingest(_message(22, event_type="update"))


def test_public_l2_book_rejects_update_before_snapshot() -> None:
    book = PublicL2Book()

    with pytest.raises(PublicL2IntegrityError, match="update before snapshot"):
        book.ingest(_message(1, event_type="update"))


def test_public_l2_replay_and_corpus_fingerprints_are_deterministic() -> None:
    left = PublicL2Book()
    right = PublicL2Book()
    snapshot = _message(30)
    update = _message(
        31,
        event_type="update",
        updates=[
            {
                "side": "bid",
                "event_time": "2026-08-31T20:00:31Z",
                "price_level": "61000.00",
                "new_quantity": "1.3",
            }
        ],
    )

    for book in (left, right):
        book.ingest(snapshot)
        book.ingest(update)

    assert left.corpus_fingerprint() == right.corpus_fingerprint()

    left_session = left.replay_session("public-l2-test", seed=7)
    right_session = right.replay_session("public-l2-test", seed=7)
    assert ReplayEngine(left_session).fingerprint() == ReplayEngine(right_session).fingerprint()
    assert [event.payload["wire_sequence"] for event in left_session.events] == [30, 31]


def test_non_level2_public_frames_are_ignored_without_sequence_mutation() -> None:
    book = PublicL2Book()

    assert book.ingest({"channel": "heartbeats"}) == ()
    assert book.last_sequence is None
