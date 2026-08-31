from __future__ import annotations

import json
from pathlib import Path

import pytest

from services.market_data.l2_corpus_replay import (
    PublicL2CorpusReplay,
    load_public_l2_corpus,
)
from services.market_data.l2_corpus_storage import (
    PublicL2CorpusError,
    PublicL2CorpusWriter,
)
from services.market_data.public_l2 import (
    PublicL2Frame,
    PublicL2IntegrityError,
    parse_public_l2_message,
)


def _frame(
    sequence: int,
    timestamp: str,
    *,
    event_type: str,
    bid: str,
    ask: str,
) -> PublicL2Frame:
    frame = parse_public_l2_message(
        {
            "channel": "l2_data",
            "timestamp": timestamp,
            "sequence_num": sequence,
            "events": [
                {
                    "type": event_type,
                    "product_id": "BTC-USD",
                    "updates": [
                        {
                            "side": "bid",
                            "event_time": timestamp,
                            "price_level": bid,
                            "new_quantity": "1.0",
                        },
                        {
                            "side": "offer",
                            "event_time": timestamp,
                            "price_level": ask,
                            "new_quantity": "1.0",
                        },
                    ],
                }
            ],
        }
    )
    assert frame is not None
    return frame


def _build_corpus(path: Path) -> None:
    writer = PublicL2CorpusWriter(
        path,
        dataset_name="coinbase-l2-replay-test",
        flush_every_records=1,
    )
    writer.append(
        _frame(
            10,
            "2026-08-31T21:00:10Z",
            event_type="snapshot",
            bid="61000.00",
            ask="61000.50",
        ),
        connection_generation=1,
    )
    writer.append(
        _frame(
            11,
            "2026-08-31T21:00:11Z",
            event_type="update",
            bid="61001.00",
            ask="61000.50",
        ),
        connection_generation=1,
    )
    writer.append(
        _frame(
            100,
            "2026-08-31T21:00:20Z",
            event_type="snapshot",
            bid="62000.00",
            ask="62000.50",
        ),
        connection_generation=2,
    )
    writer.append(
        _frame(
            101,
            "2026-08-31T21:00:21Z",
            event_type="update",
            bid="62001.00",
            ask="62000.50",
        ),
        connection_generation=2,
    )
    writer.finalize()


def test_loader_preserves_verified_record_order_and_connection_generation(
    tmp_path: Path,
) -> None:
    corpus_path = tmp_path / "public-l2.jsonl"
    _build_corpus(corpus_path)

    manifest, records = load_public_l2_corpus(corpus_path)

    assert manifest.frame_count == 4
    assert [record.record_index for record in records] == [0, 1, 2, 3]
    assert [record.connection_generation for record in records] == [1, 1, 2, 2]
    assert [record.frame.sequence for record in records] == [10, 11, 100, 101]


def test_replay_resets_book_between_connection_generations(tmp_path: Path) -> None:
    corpus_path = tmp_path / "public-l2.jsonl"
    _build_corpus(corpus_path)
    replay = PublicL2CorpusReplay(corpus_path)

    first = replay.run_all()
    second = replay.run_all()

    assert first == second
    assert len(first) == 4
    assert [item.connection_generation for item in first] == [1, 1, 2, 2]
    assert first[0].snapshot.bids[0].price == 61000.0
    assert first[1].snapshot.bids[0].price == 61001.0
    assert first[2].snapshot.bids[0].price == 62000.0
    assert first[3].snapshot.bids[0].price == 62001.0
    assert all(level.price >= 62000.0 for level in first[2].snapshot.bids)


def test_replay_session_carries_dataset_and_generation_provenance(
    tmp_path: Path,
) -> None:
    corpus_path = tmp_path / "public-l2.jsonl"
    _build_corpus(corpus_path)
    replay = PublicL2CorpusReplay(corpus_path)

    alpha = replay.replay_session("alpha", seed=7)
    beta = replay.replay_session("beta", seed=7)

    assert alpha.session_id == "alpha"
    assert beta.session_id == "beta"
    assert len(alpha.events) == 4
    assert [event.event_id for event in alpha.events] == [
        event.event_id for event in beta.events
    ]
    assert alpha.events[0].payload["dataset_content_sha256"] == (
        replay.manifest.dataset.content_sha256
    )
    assert alpha.events[0].payload["connection_generation"] == 1
    assert alpha.events[2].payload["connection_generation"] == 2
    assert alpha.events[0].stream.endswith(":g1")
    assert alpha.events[2].stream.endswith(":g2")


def test_replay_fingerprint_is_session_name_independent_and_seed_sensitive(
    tmp_path: Path,
) -> None:
    corpus_path = tmp_path / "public-l2.jsonl"
    _build_corpus(corpus_path)
    replay = PublicL2CorpusReplay(corpus_path)

    expected = replay.replay_fingerprint(seed=13)

    assert expected == replay.replay_fingerprint(seed=13)
    assert expected != replay.replay_fingerprint(seed=14)
    assert len(expected) == 64
    provenance = replay.experiment_provenance(seed=13)
    assert provenance["dataset"] == replay.manifest.experiment_dataset()
    assert provenance["replay_fingerprint"] == expected


def test_replay_rejects_tampered_corpus_before_loading(tmp_path: Path) -> None:
    corpus_path = tmp_path / "public-l2.jsonl"
    _build_corpus(corpus_path)
    lines = corpus_path.read_text(encoding="utf-8").splitlines()
    record = json.loads(lines[0])
    record["frame"]["sequence"] = 999
    lines[0] = json.dumps(record, sort_keys=True, separators=(",", ":"))
    corpus_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    with pytest.raises(PublicL2CorpusError, match="checksum"):
        PublicL2CorpusReplay(corpus_path)


def test_new_connection_generation_requires_fresh_snapshot(tmp_path: Path) -> None:
    corpus_path = tmp_path / "public-l2.jsonl"
    writer = PublicL2CorpusWriter(
        corpus_path,
        dataset_name="coinbase-l2-invalid-generation",
        flush_every_records=1,
    )
    writer.append(
        _frame(
            10,
            "2026-08-31T21:00:10Z",
            event_type="snapshot",
            bid="61000.00",
            ask="61000.50",
        ),
        connection_generation=1,
    )
    writer.append(
        _frame(
            100,
            "2026-08-31T21:00:20Z",
            event_type="update",
            bid="62000.00",
            ask="62000.50",
        ),
        connection_generation=2,
    )
    writer.finalize()
    replay = PublicL2CorpusReplay(corpus_path)

    with pytest.raises(PublicL2IntegrityError, match="update before snapshot"):
        replay.run_all()
