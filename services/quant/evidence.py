"""Durable, read-only retrievable evidence for quantitative research."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from typing import Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictInt,
    StrictStr,
    ValidationError,
    field_validator,
    model_validator,
)
from sqlalchemy import JSON, Column, DateTime, MetaData, String, Table, insert, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncEngine

from .pipeline import QuantPipelineInput, run_quant_pipeline

quant_evidence_metadata = MetaData()
quant_evidence_table = Table(
    "quant_evidence",
    quant_evidence_metadata,
    Column("id", String(64), primary_key=True),
    Column("instrument_id", String(160), nullable=False, index=True),
    Column("observed_at", DateTime(timezone=True), nullable=False, index=True),
    Column("payload", JSON, nullable=False),
)


class EvidenceCollision(RuntimeError):
    """A persisted identity does not match the requested content."""


def _finite_decimal(value: str) -> Decimal:
    if not value or len(value) > 64 or not value.isascii() or any(c.isspace() for c in value):
        raise ValueError("numeric text must be finite and bounded")
    try:
        result = Decimal(value)
    except InvalidOperation as exc:
        raise ValueError("numeric text must be finite and bounded") from exc
    if not result.is_finite():
        raise ValueError("numeric text must be finite and bounded")
    return result


class _Observed(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    source: StrictStr = Field(min_length=1, max_length=160, pattern=r"^\S(?:.*\S)?$")
    observed_at: datetime

    @field_validator("observed_at")
    @classmethod
    def _aware(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("observed_at must be timezone-aware")
        return value.astimezone(UTC)


class CostEvidence(_Observed):
    value: StrictStr

    @field_validator("value")
    @classmethod
    def _nonnegative(cls, value: str) -> str:
        if _finite_decimal(value) < 0:
            raise ValueError("cost must be nonnegative")
        return value


class CalibrationEvidence(_Observed):
    provenance: Literal["SIMULATED", "REPLAY", "PAPER"]
    probability: StrictStr
    outcome: StrictInt = Field(ge=0, le=1)

    @field_validator("probability")
    @classmethod
    def _probability(cls, value: str) -> str:
        if not 0 <= _finite_decimal(value) <= 1:
            raise ValueError("probability must be between zero and one")
        return value


class QuantEvidence(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    contract_version: Literal["quant-evidence/1"] = "quant-evidence/1"
    instrument_id: StrictStr = Field(
        min_length=3, max_length=160, pattern=r"^[A-Z0-9_]+:[A-Z0-9._-]+$"
    )
    mode: Literal["SIMULATION", "SHADOW", "HISTORICAL_REPLAY"]
    observed_at: datetime
    computed_at: datetime
    input_source: StrictStr = Field(min_length=1, max_length=160, pattern=r"^\S(?:.*\S)?$")
    input_provenance: Literal["SIMULATED", "REPLAY", "PAPER"]
    pipeline_input: QuantPipelineInput
    model_version: StrictStr = Field(min_length=1, max_length=120, pattern=r"^\S(?:.*\S)?$")
    feature_version: StrictStr = Field(min_length=1, max_length=120, pattern=r"^\S(?:.*\S)?$")
    costs: dict[
        Literal["fees", "slippage", "spread_cost", "hedge_cost", "latency_penalty"], CostEvidence
    ]
    calibration_samples: tuple[CalibrationEvidence, ...] = ()
    result: dict[StrictStr, StrictStr] = Field(min_length=1, max_length=64)
    financial_connectivity: Literal[False] = False
    real_money_execution: Literal[False] = False

    @field_validator("observed_at", "computed_at")
    @classmethod
    def _aware(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("observed_at must be timezone-aware")
        return value.astimezone(UTC)

    @model_validator(mode="after")
    def _lineage(self) -> QuantEvidence:
        required = {"fees", "slippage", "spread_cost", "hedge_cost", "latency_penalty"}
        if set(self.costs) != required:
            raise ValueError("all cost provenance is required")
        if self.computed_at < self.observed_at:
            raise ValueError("computed_at cannot precede observed_at")
        if self.pipeline_input.observed_at != self.observed_at:
            raise ValueError("pipeline input timestamp differs from evidence")
        if any(cost.observed_at > self.observed_at for cost in self.costs.values()):
            raise ValueError("cost evidence cannot be from the future")
        if any(sample.observed_at >= self.observed_at for sample in self.calibration_samples):
            raise ValueError("calibration samples must be strictly earlier")
        for key, cost in self.costs.items():
            if Decimal(str(getattr(self.pipeline_input, key))) != _finite_decimal(cost.value):
                raise ValueError("pipeline cost differs from provenance")
        samples = self.pipeline_input.calibration_samples
        if len(samples) != len(self.calibration_samples) or any(
            Decimal(str(actual.probability)) != _finite_decimal(evidence.probability)
            or actual.outcome != evidence.outcome
            for actual, evidence in zip(samples, self.calibration_samples, strict=True)
        ):
            raise ValueError("pipeline calibration differs from provenance")
        for key, value in self.result.items():
            if not key or len(key) > 64:
                raise ValueError("invalid numeric field key")
            _finite_decimal(value)
        return self


def _payload(envelope: QuantEvidence) -> tuple[str, dict]:
    payload = envelope.model_dump(mode="json")
    encoded = json.dumps(
        payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
    )
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest(), payload


def _validated_row(row) -> dict:
    try:
        envelope = QuantEvidence.model_validate(row.payload)
        identity, payload = _payload(envelope)
        stored_time = row.observed_at
        if stored_time.tzinfo is None:
            stored_time = stored_time.replace(tzinfo=UTC)
        if (
            identity != row.id
            or row.instrument_id != envelope.instrument_id
            or stored_time.astimezone(UTC) != envelope.observed_at
            or payload != row.payload
        ):
            raise ValueError("stored identity or index differs")
    except (ValidationError, ValueError, TypeError, AttributeError) as exc:
        raise EvidenceCollision("quant evidence identity collision or corrupt record") from exc
    return payload


async def store_quant_evidence(engine: AsyncEngine | None, envelope: QuantEvidence) -> str:
    if engine is None:
        raise LookupError("quant evidence storage unavailable")
    envelope = QuantEvidence.model_validate(envelope.model_dump(mode="json"))
    if envelope.computed_at > datetime.now(UTC):
        raise ValueError("computed_at cannot be in the future")
    result = run_quant_pipeline(envelope.pipeline_input)
    expected = {
        "raw_probability": str(result.raw_probability),
        "fair_probability": str(result.fair_probability),
        "confidence": str(result.confidence),
        "uncertainty": str(result.uncertainty),
        "net_edge": str(result.edge.net_edge),
        "risk_adjusted_ev": str(result.expected_value.risk_adjusted_ev),
    }
    if (
        envelope.result != expected
        or envelope.model_version != result.model_version
        or envelope.feature_version != result.feature_version
    ):
        raise ValueError("pipeline result or version differs from evidence")
    identity, payload = _payload(envelope)
    try:
        async with engine.begin() as connection:
            await connection.execute(
                insert(quant_evidence_table).values(
                    id=identity,
                    instrument_id=envelope.instrument_id,
                    observed_at=envelope.observed_at,
                    payload=payload,
                )
            )
    except IntegrityError as exc:
        async with engine.connect() as connection:
            row = (
                await connection.execute(
                    select(quant_evidence_table).where(quant_evidence_table.c.id == identity)
                )
            ).one_or_none()
        if row is None:
            raise
        if _validated_row(row) != payload:
            raise EvidenceCollision("quant evidence identity collision") from exc
    return identity


async def recent_quant_evidence(
    engine: AsyncEngine | None, *, limit: int = 20, instrument_id: str | None = None
) -> list[dict]:
    if engine is None:
        raise LookupError("quant evidence storage unavailable")
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 100:
        raise ValueError("limit must be between 1 and 100")
    query = select(quant_evidence_table)
    if instrument_id is not None:
        query = query.where(quant_evidence_table.c.instrument_id == instrument_id)
    query = query.order_by(
        quant_evidence_table.c.observed_at.desc(), quant_evidence_table.c.id.desc()
    ).limit(limit)
    async with engine.connect() as connection:
        rows = (await connection.execute(query)).all()
    return [{"evidence_id": row.id, **_validated_row(row)} for row in rows]
