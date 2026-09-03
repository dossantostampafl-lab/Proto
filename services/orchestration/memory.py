from __future__ import annotations

import json
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import DateTime, String, Text, select, update
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class DecisionMemoryBase(DeclarativeBase):
    pass


class DecisionStage(StrEnum):
    PROPOSED = "PROPOSED"
    RISK_APPROVED = "RISK_APPROVED"
    RISK_REJECTED = "RISK_REJECTED"
    PAPER_EXECUTED = "PAPER_EXECUTED"
    SHADOW_ONLY = "SHADOW_ONLY"
    COMPLETED = "COMPLETED"


class DecisionMemoryEntry(BaseModel):
    """Fact-only decision lineage.

    Fields that are not observed or produced by a real PROTO component remain
    null instead of being synthesized. The record therefore preserves absence
    of evidence as first-class provenance.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False)

    decision_id: UUID
    mission_id: UUID | None = None
    instrument_id: str = Field(min_length=3, max_length=160)
    observed_at: datetime
    recorded_at: datetime
    stage: DecisionStage
    model_id: str | None = Field(default=None, max_length=160)
    model_version: str | None = Field(default=None, max_length=160)
    feature_version: str | None = Field(default=None, max_length=160)
    calibration_version: str | None = Field(default=None, max_length=160)
    input_hash: str = Field(min_length=16, max_length=256)
    regime: str | None = Field(default=None, max_length=120)
    probability: float | None = Field(default=None, ge=0.0, le=1.0)
    uncertainty: float | None = Field(default=None, ge=0.0, le=1.0)
    edge: float | None = None
    risk_decision: str | None = Field(default=None, max_length=120)
    proposed_action: str | None = Field(default=None, max_length=120)
    actual_action: str | None = Field(default=None, max_length=120)
    explanation: str | None = Field(default=None, max_length=4_000)
    provenance: dict[str, Any] = Field(default_factory=dict)

    @field_validator("observed_at", "recorded_at")
    @classmethod
    def require_aware_timestamp(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("decision timestamps must be timezone-aware")
        return value.astimezone(UTC)

    @field_validator("instrument_id")
    @classmethod
    def require_namespaced_instrument(cls, value: str) -> str:
        normalized = value.strip().upper()
        if ":" not in normalized:
            raise ValueError("instrument_id must be namespaced")
        return normalized


class DecisionOutcome(BaseModel):
    model_config = ConfigDict(frozen=True, allow_inf_nan=False)

    decision_id: UUID
    resolved_at: datetime
    outcome: str = Field(min_length=1, max_length=240)
    pnl: float | None = None
    calibration_error: float | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("resolved_at")
    @classmethod
    def require_aware_timestamp(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("resolved_at must be timezone-aware")
        return value.astimezone(UTC)


class DecisionMemoryRecord(DecisionMemoryBase):
    __tablename__ = "decision_memory"

    decision_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    mission_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    instrument_id: Mapped[str] = mapped_column(String(160), index=True)
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    stage: Mapped[str] = mapped_column(String(40), index=True)
    entry_json: Mapped[str] = mapped_column(Text)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    outcome_json: Mapped[str | None] = mapped_column(Text, nullable=True)


class DecisionMemoryStore:
    def __init__(self, engine: AsyncEngine) -> None:
        self.engine = engine
        self.session_factory = async_sessionmaker(engine, expire_on_commit=False)

    async def init_schema(self) -> None:
        async with self.engine.begin() as connection:
            await connection.run_sync(DecisionMemoryBase.metadata.create_all)

    async def record(self, entry: DecisionMemoryEntry) -> DecisionMemoryEntry:
        serialized = entry.model_dump_json()
        key = str(entry.decision_id)
        async with self.session_factory() as session:
            existing = await session.get(DecisionMemoryRecord, key)
            if existing is not None:
                stored = DecisionMemoryEntry.model_validate_json(existing.entry_json)
                if stored != entry:
                    raise ValueError("decision_id already belongs to a different decision record")
                return stored
            session.add(
                DecisionMemoryRecord(
                    decision_id=key,
                    mission_id=str(entry.mission_id) if entry.mission_id else None,
                    instrument_id=entry.instrument_id,
                    observed_at=entry.observed_at,
                    recorded_at=entry.recorded_at,
                    stage=entry.stage.value,
                    entry_json=serialized,
                )
            )
            await session.commit()
            return entry

    async def record_outcome(self, outcome: DecisionOutcome) -> DecisionOutcome:
        key = str(outcome.decision_id)
        serialized = outcome.model_dump_json()
        async with self.session_factory() as session:
            record = await session.get(DecisionMemoryRecord, key)
            if record is None:
                raise KeyError(f"unknown decision_id: {key}")
            if record.outcome_json is not None:
                stored = DecisionOutcome.model_validate_json(record.outcome_json)
                if stored != outcome:
                    raise ValueError("decision outcome is immutable once recorded")
                return stored
            observed_at = record.observed_at
            if observed_at.tzinfo is None:
                observed_at = observed_at.replace(tzinfo=UTC)
            if outcome.resolved_at < observed_at.astimezone(UTC):
                raise ValueError("decision outcome cannot predate the decision observation")
            await session.execute(
                update(DecisionMemoryRecord)
                .where(
                    DecisionMemoryRecord.decision_id == key,
                    DecisionMemoryRecord.outcome_json.is_(None),
                )
                .values(
                    resolved_at=outcome.resolved_at,
                    outcome_json=serialized,
                )
                .execution_options(synchronize_session=False)
            )
            await session.commit()
            return outcome

    async def get(
        self, decision_id: UUID
    ) -> tuple[DecisionMemoryEntry, DecisionOutcome | None] | None:
        async with self.session_factory() as session:
            record = await session.get(DecisionMemoryRecord, str(decision_id))
            if record is None:
                return None
            entry = DecisionMemoryEntry.model_validate_json(record.entry_json)
            outcome = (
                DecisionOutcome.model_validate_json(record.outcome_json)
                if record.outcome_json is not None
                else None
            )
            return entry, outcome

    async def recent(
        self,
        *,
        instrument_id: str | None = None,
        limit: int = 100,
    ) -> list[tuple[DecisionMemoryEntry, DecisionOutcome | None]]:
        safe_limit = min(max(limit, 1), 1_000)
        statement = select(DecisionMemoryRecord)
        if instrument_id is not None:
            statement = statement.where(
                DecisionMemoryRecord.instrument_id == instrument_id.strip().upper()
            )
        statement = statement.order_by(DecisionMemoryRecord.recorded_at.desc()).limit(safe_limit)
        async with self.session_factory() as session:
            records = (await session.scalars(statement)).all()
            return [
                (
                    DecisionMemoryEntry.model_validate_json(record.entry_json),
                    DecisionOutcome.model_validate_json(record.outcome_json)
                    if record.outcome_json is not None
                    else None,
                )
                for record in records
            ]

    async def snapshot(self) -> dict[str, object]:
        async with self.session_factory() as session:
            records = (await session.scalars(select(DecisionMemoryRecord))).all()
        resolved = sum(record.outcome_json is not None for record in records)
        return {
            "records": len(records),
            "resolved": resolved,
            "unresolved": len(records) - resolved,
            "financial_connectivity": False,
            "real_money_execution": False,
        }
