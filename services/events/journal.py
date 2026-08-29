from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from enum import StrEnum
from uuid import UUID, uuid4

from pydantic import BaseModel, Field


class ResearchEventType(StrEnum):
    MARKET_DATA = "MARKET_DATA"
    MODEL_ESTIMATE = "MODEL_ESTIMATE"
    EDGE_EVALUATION = "EDGE_EVALUATION"
    RISK_STATE = "RISK_STATE"
    SIMULATED_FILL = "SIMULATED_FILL"


class ResearchEvent(BaseModel):
    event_id: UUID = Field(default_factory=uuid4)
    event_type: ResearchEventType
    occurred_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    payload: dict[str, object] = Field(default_factory=dict)


class JournalRecord(BaseModel):
    index: int = Field(ge=0)
    event: ResearchEvent
    previous_hash: str
    record_hash: str


class HashChainJournal:
    def __init__(self, max_records: int = 10_000) -> None:
        self.max_records = max_records
        self._records: list[JournalRecord] = []

    @staticmethod
    def _event_bytes(index: int, previous_hash: str, event: ResearchEvent) -> bytes:
        canonical = json.dumps(
            {
                "index": index,
                "previous_hash": previous_hash,
                "event": event.model_dump(mode="json"),
            },
            separators=(",", ":"),
            sort_keys=True,
        )
        return canonical.encode("utf-8")

    @classmethod
    def _hash_record(cls, index: int, previous_hash: str, event: ResearchEvent) -> str:
        return hashlib.sha256(cls._event_bytes(index, previous_hash, event)).hexdigest()

    def append(self, event: ResearchEvent) -> JournalRecord:
        if len(self._records) >= self.max_records:
            raise ValueError("journal capacity exceeded")

        index = len(self._records)
        previous_hash = self._records[-1].record_hash if self._records else "GENESIS"
        record = JournalRecord(
            index=index,
            event=event,
            previous_hash=previous_hash,
            record_hash=self._hash_record(index, previous_hash, event),
        )
        self._records.append(record)
        return record

    def list(self, limit: int = 100) -> list[JournalRecord]:
        safe_limit = min(max(limit, 1), self.max_records)
        return self._records[-safe_limit:]

    def verify(self) -> bool:
        previous_hash = "GENESIS"
        for index, record in enumerate(self._records):
            if record.index != index or record.previous_hash != previous_hash:
                return False
            expected_hash = self._hash_record(index, previous_hash, record.event)
            if record.record_hash != expected_hash:
                return False
            previous_hash = record.record_hash
        return True

    def reset(self) -> None:
        self._records.clear()
