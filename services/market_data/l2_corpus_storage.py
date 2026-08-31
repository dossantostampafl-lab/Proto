from __future__ import annotations

import json
import os
from collections.abc import Mapping
from datetime import datetime
from hashlib import sha256
from pathlib import Path
from typing import Any, Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .public_l2 import PublicL2Frame, parse_public_l2_message

CORPUS_FORMAT_VERSION = "proto-public-l2-jsonl-v1"
CORPUS_SCHEMA_VERSION = "coinbase-public-l2-normalized-v1"
CORPUS_SOURCE = "coinbase_advanced_trade_public_ws"
CORPUS_VENUE = "COINBASE"


class PublicL2CorpusError(RuntimeError):
    """Raised when a public L2 corpus cannot be persisted or verified safely."""


class PublicL2CorpusSink(Protocol):
    def append_message(
        self,
        message: str | bytes | dict[str, Any],
        *,
        connection_generation: int,
    ) -> None:
        """Append one validated public L2 wire message to a research corpus."""


class PublicL2DatasetProvenance(BaseModel):
    model_config = ConfigDict(frozen=True)

    name: str = Field(min_length=1, max_length=120)
    source: str = CORPUS_SOURCE
    venue: str = CORPUS_VENUE
    data_level: Literal["L2"] = "L2"
    content_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    schema_version: str = CORPUS_SCHEMA_VERSION
    symbols: tuple[str, ...] = Field(min_length=1)
    start_at: datetime
    end_at: datetime
    event_count: int = Field(gt=0)
    quality: dict[str, object]

    @model_validator(mode="after")
    def validate_coverage(self) -> PublicL2DatasetProvenance:
        for value in (self.start_at, self.end_at):
            if value.tzinfo is None or value.utcoffset() is None:
                raise ValueError("public L2 dataset timestamps must be timezone-aware")
        if self.start_at >= self.end_at:
            raise ValueError("public L2 dataset start_at must be before end_at")
        return self


class PublicL2CorpusManifest(BaseModel):
    model_config = ConfigDict(frozen=True)

    format_version: str = CORPUS_FORMAT_VERSION
    dataset: PublicL2DatasetProvenance
    corpus_file: str = Field(min_length=1)
    frame_count: int = Field(gt=0)
    connection_generations: tuple[int, ...] = Field(min_length=1)
    chain_head: str = Field(pattern=r"^[0-9a-f]{64}$")

    def experiment_dataset(self) -> dict[str, object]:
        """Return the exact dataset payload accepted by Experiment Registry."""
        return self.dataset.model_dump(mode="json")


def _canonical_json(payload: Mapping[str, object]) -> str:
    return json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    )


def _record_core(
    *,
    index: int,
    connection_generation: int,
    frame: PublicL2Frame,
    previous_hash: str,
) -> dict[str, object]:
    return {
        "format_version": CORPUS_FORMAT_VERSION,
        "record_index": index,
        "connection_generation": connection_generation,
        "previous_hash": previous_hash,
        "frame": frame.model_dump(mode="json"),
    }


def _record_hash(core: Mapping[str, object]) -> str:
    return sha256(_canonical_json(core).encode()).hexdigest()


class PublicL2CorpusWriter:
    """Append-only, hash-chained JSONL writer for validated public L2 frames."""

    def __init__(
        self,
        path: str | Path,
        *,
        dataset_name: str,
        flush_every_records: int = 100,
    ) -> None:
        if not dataset_name.strip():
            raise ValueError("dataset_name must not be empty")
        if len(dataset_name) > 120:
            raise ValueError("dataset_name exceeds 120 characters")
        if (
            isinstance(flush_every_records, bool)
            or not isinstance(flush_every_records, int)
            or flush_every_records <= 0
        ):
            raise ValueError("flush_every_records must be a positive integer")

        self.path = Path(path)
        self.manifest_path = self.path.with_name(f"{self.path.name}.manifest.json")
        if self.path.exists() or self.manifest_path.exists():
            raise FileExistsError("public L2 corpus artifacts already exist")
        self.path.parent.mkdir(parents=True, exist_ok=True)

        self.dataset_name = dataset_name.strip()
        self.flush_every_records = flush_every_records
        self._handle = self.path.open("x", encoding="utf-8", newline="\n")
        self._content_digest = sha256()
        self._frame_count = 0
        self._event_count = 0
        self._previous_hash = "0" * 64
        self._symbols: set[str] = set()
        self._generations: set[int] = set()
        self._last_sequence_by_generation: dict[int, int] = {}
        self._start_at: datetime | None = None
        self._end_at: datetime | None = None
        self._manifest: PublicL2CorpusManifest | None = None

    @property
    def closed(self) -> bool:
        return self._handle.closed

    def _flush(self) -> None:
        self._handle.flush()
        os.fsync(self._handle.fileno())

    def append_message(
        self,
        message: str | bytes | dict[str, Any],
        *,
        connection_generation: int,
    ) -> None:
        frame = parse_public_l2_message(message)
        if frame is None:
            return
        self.append(frame, connection_generation=connection_generation)

    def append(self, frame: PublicL2Frame, *, connection_generation: int) -> None:
        if self.closed:
            raise PublicL2CorpusError("public L2 corpus writer is closed")
        if (
            isinstance(connection_generation, bool)
            or not isinstance(connection_generation, int)
            or connection_generation <= 0
        ):
            raise ValueError("connection_generation must be a positive integer")
        if not frame.events:
            return

        previous_sequence = self._last_sequence_by_generation.get(connection_generation)
        if previous_sequence is not None and frame.sequence != previous_sequence + 1:
            raise PublicL2CorpusError(
                "public L2 corpus sequence is not contiguous within connection generation"
            )
        if self._end_at is not None and frame.timestamp < self._end_at:
            raise PublicL2CorpusError("public L2 corpus timestamps must not regress")

        core = _record_core(
            index=self._frame_count,
            connection_generation=connection_generation,
            frame=frame,
            previous_hash=self._previous_hash,
        )
        record_hash = _record_hash(core)
        record = {**core, "record_hash": record_hash}
        line = _canonical_json(record) + "\n"
        self._handle.write(line)
        self._content_digest.update(line.encode())

        self._frame_count += 1
        self._event_count += len(frame.events)
        self._previous_hash = record_hash
        self._generations.add(connection_generation)
        self._last_sequence_by_generation[connection_generation] = frame.sequence
        self._start_at = frame.timestamp if self._start_at is None else self._start_at
        self._end_at = frame.timestamp
        for event in frame.events:
            self._symbols.add(event.product_id)

        if self._frame_count % self.flush_every_records == 0:
            self._flush()

    def finalize(self) -> PublicL2CorpusManifest:
        if self._manifest is not None:
            return self._manifest
        if self.closed:
            raise PublicL2CorpusError("public L2 corpus writer closed before finalize")
        if self._frame_count == 0 or self._start_at is None or self._end_at is None:
            raise PublicL2CorpusError("public L2 corpus contains no research frames")
        if self._start_at >= self._end_at:
            raise PublicL2CorpusError(
                "public L2 corpus requires positive temporal coverage for experiments"
            )

        self._flush()
        self._handle.close()
        dataset = PublicL2DatasetProvenance(
            name=self.dataset_name,
            content_sha256=self._content_digest.hexdigest(),
            symbols=tuple(sorted(self._symbols)),
            start_at=self._start_at,
            end_at=self._end_at,
            event_count=self._event_count,
            quality={
                "gaps": 0,
                "sequence_valid": True,
                "hash_chain_valid": True,
                "frame_count": self._frame_count,
                "connection_generations": len(self._generations),
            },
        )
        manifest = PublicL2CorpusManifest(
            dataset=dataset,
            corpus_file=self.path.name,
            frame_count=self._frame_count,
            connection_generations=tuple(sorted(self._generations)),
            chain_head=self._previous_hash,
        )
        manifest_text = json.dumps(
            manifest.model_dump(mode="json"),
            sort_keys=True,
            indent=2,
            ensure_ascii=True,
            allow_nan=False,
        )
        self.manifest_path.write_text(manifest_text + "\n", encoding="utf-8")
        self._manifest = manifest
        return manifest

    def close(self) -> None:
        if not self.closed:
            self._flush()
            self._handle.close()


def verify_public_l2_corpus(
    path: str | Path,
    *,
    manifest_path: str | Path | None = None,
) -> PublicL2CorpusManifest:
    corpus_path = Path(path)
    resolved_manifest_path = (
        Path(manifest_path)
        if manifest_path is not None
        else corpus_path.with_name(f"{corpus_path.name}.manifest.json")
    )
    try:
        raw = corpus_path.read_bytes()
        manifest = PublicL2CorpusManifest.model_validate_json(
            resolved_manifest_path.read_text(encoding="utf-8")
        )
    except (OSError, ValueError) as error:
        raise PublicL2CorpusError("public L2 corpus artifacts cannot be read") from error

    if manifest.corpus_file != corpus_path.name:
        raise PublicL2CorpusError("public L2 corpus manifest filename mismatch")
    if sha256(raw).hexdigest() != manifest.dataset.content_sha256:
        raise PublicL2CorpusError("public L2 corpus content checksum mismatch")

    previous_hash = "0" * 64
    frame_count = 0
    event_count = 0
    symbols: set[str] = set()
    generations: set[int] = set()
    last_sequence_by_generation: dict[int, int] = {}
    start_at: datetime | None = None
    end_at: datetime | None = None

    try:
        lines = raw.decode("utf-8").splitlines()
    except UnicodeDecodeError as error:
        raise PublicL2CorpusError("public L2 corpus is not valid UTF-8") from error

    for index, line in enumerate(lines):
        try:
            record = json.loads(line)
        except json.JSONDecodeError as error:
            raise PublicL2CorpusError("public L2 corpus record is invalid JSON") from error
        if not isinstance(record, dict):
            raise PublicL2CorpusError("public L2 corpus record must be an object")
        if record.get("format_version") != CORPUS_FORMAT_VERSION:
            raise PublicL2CorpusError("public L2 corpus format version mismatch")
        if record.get("record_index") != index:
            raise PublicL2CorpusError("public L2 corpus record index mismatch")
        generation = record.get("connection_generation")
        if isinstance(generation, bool) or not isinstance(generation, int) or generation <= 0:
            raise PublicL2CorpusError("public L2 corpus connection generation is invalid")
        if record.get("previous_hash") != previous_hash:
            raise PublicL2CorpusError("public L2 corpus hash chain is broken")
        observed_record_hash = record.get("record_hash")
        core = {key: value for key, value in record.items() if key != "record_hash"}
        expected_record_hash = _record_hash(core)
        if observed_record_hash != expected_record_hash:
            raise PublicL2CorpusError("public L2 corpus record hash mismatch")
        try:
            frame = PublicL2Frame.model_validate(record.get("frame"))
        except ValueError as error:
            raise PublicL2CorpusError("public L2 corpus frame is invalid") from error
        if not frame.events:
            raise PublicL2CorpusError("public L2 corpus must not store empty frames")

        prior_sequence = last_sequence_by_generation.get(generation)
        if prior_sequence is not None and frame.sequence != prior_sequence + 1:
            raise PublicL2CorpusError(
                "public L2 corpus sequence is not contiguous within connection generation"
            )
        if end_at is not None and frame.timestamp < end_at:
            raise PublicL2CorpusError("public L2 corpus timestamps regress")

        frame_count += 1
        event_count += len(frame.events)
        generations.add(generation)
        last_sequence_by_generation[generation] = frame.sequence
        start_at = frame.timestamp if start_at is None else start_at
        end_at = frame.timestamp
        previous_hash = expected_record_hash
        for event in frame.events:
            symbols.add(event.product_id)

    if frame_count != manifest.frame_count:
        raise PublicL2CorpusError("public L2 corpus frame count mismatch")
    if event_count != manifest.dataset.event_count:
        raise PublicL2CorpusError("public L2 corpus event count mismatch")
    if tuple(sorted(symbols)) != manifest.dataset.symbols:
        raise PublicL2CorpusError("public L2 corpus symbols mismatch")
    if tuple(sorted(generations)) != manifest.connection_generations:
        raise PublicL2CorpusError("public L2 corpus generation set mismatch")
    if previous_hash != manifest.chain_head:
        raise PublicL2CorpusError("public L2 corpus chain head mismatch")
    if start_at != manifest.dataset.start_at or end_at != manifest.dataset.end_at:
        raise PublicL2CorpusError("public L2 corpus temporal coverage mismatch")

    return manifest
