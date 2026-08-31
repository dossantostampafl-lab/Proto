from __future__ import annotations

import json
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from services.replay import ReplayEvent, ReplayPhase, ReplaySession

from .contracts import OrderBookSnapshot
from .l2_corpus_storage import (
    CORPUS_FORMAT_VERSION,
    PublicL2CorpusError,
    PublicL2CorpusManifest,
    verify_public_l2_corpus,
)
from .public_feed_parser import MAX_EVENTS_PER_FRAME
from .public_l2 import PublicL2Book, PublicL2Frame


class PublicL2CorpusRecord(BaseModel):
    """One verified persisted public L2 frame with connection provenance."""

    model_config = ConfigDict(frozen=True)

    format_version: str
    record_index: int = Field(ge=0)
    connection_generation: int = Field(gt=0)
    previous_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    frame: PublicL2Frame
    record_hash: str = Field(pattern=r"^[0-9a-f]{64}$")


@dataclass(frozen=True, slots=True)
class PublicL2ReplaySnapshot:
    record_index: int
    connection_generation: int
    wire_sequence: int
    snapshot: OrderBookSnapshot


def load_public_l2_corpus(
    path: str | Path,
    *,
    manifest_path: str | Path | None = None,
) -> tuple[PublicL2CorpusManifest, tuple[PublicL2CorpusRecord, ...]]:
    """Verify a persisted corpus before exposing immutable replay records."""

    corpus_path = Path(path)
    manifest = verify_public_l2_corpus(corpus_path, manifest_path=manifest_path)
    if manifest.corpus_file != corpus_path.name:
        raise PublicL2CorpusError("public L2 corpus manifest file name mismatch")

    try:
        lines = corpus_path.read_text(encoding="utf-8").splitlines()
        records = tuple(
            PublicL2CorpusRecord.model_validate_json(line)
            for line in lines
            if line.strip()
        )
    except (OSError, ValueError) as error:
        raise PublicL2CorpusError("verified public L2 corpus cannot be loaded") from error

    if len(records) != manifest.frame_count:
        raise PublicL2CorpusError("public L2 replay record count mismatch")
    for index, record in enumerate(records):
        if record.format_version != CORPUS_FORMAT_VERSION:
            raise PublicL2CorpusError("public L2 replay format version mismatch")
        if record.record_index != index:
            raise PublicL2CorpusError("public L2 replay record order mismatch")
    return manifest, records


def _frame_to_wire(frame: PublicL2Frame) -> dict[str, object]:
    return {
        "channel": "l2_data",
        "timestamp": frame.timestamp.isoformat(),
        "sequence_num": frame.sequence,
        "events": [
            {
                "type": event.event_type,
                "product_id": event.product_id,
                "updates": [
                    {
                        "side": update.side,
                        "event_time": update.event_time.isoformat(),
                        "price_level": str(update.price),
                        "new_quantity": str(update.quantity),
                    }
                    for update in event.updates
                ],
            }
            for event in frame.events
        ],
    }


def _event_payload(
    record: PublicL2CorpusRecord,
    *,
    event_index: int,
    dataset_content_sha256: str,
) -> dict[str, object]:
    event = record.frame.events[event_index]
    return {
        "channel": "l2_data",
        "dataset_content_sha256": dataset_content_sha256,
        "record_index": record.record_index,
        "event_index": event_index,
        "connection_generation": record.connection_generation,
        "wire_sequence": record.frame.sequence,
        "product_id": event.product_id,
        "asset": event.asset,
        "event_type": event.event_type,
        "updates": [
            {
                "side": update.side,
                "price_level": str(update.price),
                "new_quantity": str(update.quantity),
                "event_time": update.event_time.isoformat(),
            }
            for update in event.updates
        ],
    }


class PublicL2CorpusReplay:
    """Offline deterministic reconstruction and ReplaySession adapter for L2 corpora."""

    def __init__(
        self,
        path: str | Path,
        *,
        manifest_path: str | Path | None = None,
        snapshot_depth: int = 1_000,
    ) -> None:
        self.path = Path(path)
        self.manifest, self.records = load_public_l2_corpus(
            self.path,
            manifest_path=manifest_path,
        )
        self.snapshot_depth = snapshot_depth

    def run_all(self) -> tuple[PublicL2ReplaySnapshot, ...]:
        book = PublicL2Book(snapshot_depth=self.snapshot_depth)
        current_generation: int | None = None
        output: list[PublicL2ReplaySnapshot] = []

        for record in self.records:
            if record.connection_generation != current_generation:
                book.reset()
                current_generation = record.connection_generation
            snapshots = book.ingest(_frame_to_wire(record.frame))
            for snapshot in snapshots:
                output.append(
                    PublicL2ReplaySnapshot(
                        record_index=record.record_index,
                        connection_generation=record.connection_generation,
                        wire_sequence=record.frame.sequence,
                        snapshot=snapshot,
                    )
                )
        return tuple(output)

    def replay_session(self, session_id: str, *, seed: int = 0) -> ReplaySession:
        events: list[ReplayEvent] = []
        dataset_sha = self.manifest.dataset.content_sha256
        for record in self.records:
            for event_index, event in enumerate(record.frame.events):
                payload = _event_payload(
                    record,
                    event_index=event_index,
                    dataset_content_sha256=dataset_sha,
                )
                canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
                event_id = sha256(canonical.encode("utf-8")).hexdigest()
                events.append(
                    ReplayEvent(
                        event_id=event_id,
                        observed_at=record.frame.timestamp,
                        phase=ReplayPhase.MARKET_DATA,
                        stream=(
                            f"coinbase-public-l2:{event.asset}:"
                            f"g{record.connection_generation}"
                        ),
                        sequence=(
                            record.frame.sequence * MAX_EVENTS_PER_FRAME + event_index
                        ),
                        event_type=f"public_l2.{event.event_type}",
                        payload=payload,
                    )
                )
        return ReplaySession(session_id=session_id, seed=seed, events=tuple(events))

    def replay_fingerprint(self, *, seed: int = 0) -> str:
        """Fingerprint replay evidence independently of an arbitrary session name."""

        session = self.replay_session("fingerprint", seed=seed)
        canonical = json.dumps(
            {
                "dataset_content_sha256": self.manifest.dataset.content_sha256,
                "seed": seed,
                "events": [event.model_dump(mode="json") for event in session.events],
            },
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
            allow_nan=False,
        )
        return sha256(canonical.encode("utf-8")).hexdigest()

    def experiment_provenance(self, *, seed: int = 0) -> dict[str, object]:
        return {
            "dataset": self.manifest.experiment_dataset(),
            "replay_fingerprint": self.replay_fingerprint(seed=seed),
        }
