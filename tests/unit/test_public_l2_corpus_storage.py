from __future__ import annotations

from hashlib import sha256
from pathlib import Path

import pytest

from services.market_data import (
    PublicL2CorpusError,
    PublicL2CorpusWriter,
    parse_public_l2_message,
    verify_public_l2_corpus,
)


def _frame(sequence: int, second: int, *, product_id: str = "BTC-USD"):
    frame = parse_public_l2_message(
        {
            "channel": "l2_data",
            "timestamp": f"2026-08-31T22:00:{second:02d}Z",
            "sequence_num": sequence,
            "events": [
                {
                    "type": "snapshot" if sequence in {10, 20, 100} else "update",
                    "product_id": product_id,
                    "updates": [
                        {
                            "side": "bid",
                            "event_time": f"2026-08-31T22:00:{second:02d}Z",
                            "price_level": "61000.00",
                            "new_quantity": "1.2",
                        },
                        {
                            "side": "offer",
                            "event_time": f"2026-08-31T22:00:{second:02d}Z",
                            "price_level": "61000.50",
                            "new_quantity": "0.8",
                        },
                    ],
                }
            ],
        }
    )
    assert frame is not None
    return frame


def test_public_l2_corpus_round_trip_and_experiment_dataset_manifest(tmp_path: Path) -> None:
    path = tmp_path / "coinbase-l2.jsonl"
    writer = PublicL2CorpusWriter(
        path,
        dataset_name="coinbase-btc-l2-2026-08-31",
        flush_every_records=1,
    )
    writer.append(_frame(10, 10), connection_generation=1)
    writer.append(_frame(11, 11), connection_generation=1)

    manifest = writer.finalize()

    assert manifest.corpus_file == path.name
    assert manifest.frame_count == 2
    assert manifest.connection_generations == (1,)
    assert manifest.dataset.name == "coinbase-btc-l2-2026-08-31"
    assert manifest.dataset.source == "coinbase_advanced_trade_public_ws"
    assert manifest.dataset.venue == "COINBASE"
    assert manifest.dataset.data_level == "L2"
    assert manifest.dataset.schema_version == "coinbase-public-l2-normalized-v1"
    assert manifest.dataset.symbols == ("BTC-USD",)
    assert manifest.dataset.event_count == 2
    assert manifest.dataset.quality["gaps"] == 0
    assert manifest.dataset.quality["sequence_valid"] is True
    assert manifest.dataset.quality["hash_chain_valid"] is True
    assert manifest.dataset.content_sha256 == sha256(path.read_bytes()).hexdigest()

    experiment_dataset = manifest.experiment_dataset()
    assert experiment_dataset["content_sha256"] == manifest.dataset.content_sha256
    assert experiment_dataset["data_level"] == "L2"
    assert experiment_dataset["symbols"] == ["BTC-USD"]

    verified = verify_public_l2_corpus(path)
    assert verified == manifest


def test_public_l2_corpus_allows_sequence_restart_on_new_connection_generation(
    tmp_path: Path,
) -> None:
    path = tmp_path / "reconnect.jsonl"
    writer = PublicL2CorpusWriter(path, dataset_name="reconnect-corpus")
    writer.append(_frame(20, 20), connection_generation=1)
    writer.append(_frame(100, 40), connection_generation=2)

    manifest = writer.finalize()

    assert manifest.connection_generations == (1, 2)
    assert manifest.dataset.quality["connection_generations"] == 2
    assert verify_public_l2_corpus(path) == manifest


def test_public_l2_corpus_rejects_non_contiguous_sequence_within_generation(
    tmp_path: Path,
) -> None:
    writer = PublicL2CorpusWriter(
        tmp_path / "gap.jsonl",
        dataset_name="gap-corpus",
    )
    writer.append(_frame(10, 10), connection_generation=1)

    with pytest.raises(PublicL2CorpusError, match="not contiguous"):
        writer.append(_frame(12, 12), connection_generation=1)

    writer.close()


def test_public_l2_corpus_verifier_detects_tampering(tmp_path: Path) -> None:
    path = tmp_path / "tampered.jsonl"
    writer = PublicL2CorpusWriter(path, dataset_name="tamper-corpus")
    writer.append(_frame(10, 10), connection_generation=1)
    writer.append(_frame(11, 11), connection_generation=1)
    writer.finalize()

    path.write_text(path.read_text(encoding="utf-8") + "{}\n", encoding="utf-8")

    with pytest.raises(PublicL2CorpusError, match="checksum mismatch"):
        verify_public_l2_corpus(path)


def test_public_l2_corpus_refuses_existing_artifacts(tmp_path: Path) -> None:
    path = tmp_path / "existing.jsonl"
    path.write_text("already-here\n", encoding="utf-8")

    with pytest.raises(FileExistsError, match="already exist"):
        PublicL2CorpusWriter(path, dataset_name="existing-corpus")


def test_public_l2_corpus_requires_positive_temporal_coverage(tmp_path: Path) -> None:
    writer = PublicL2CorpusWriter(
        tmp_path / "single.jsonl",
        dataset_name="single-frame-corpus",
    )
    writer.append(_frame(10, 10), connection_generation=1)

    with pytest.raises(PublicL2CorpusError, match="positive temporal coverage"):
        writer.finalize()

    writer.close()
