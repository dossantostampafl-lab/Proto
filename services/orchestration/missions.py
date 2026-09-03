from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .runtime import SAFE_MODES, ProtoBrain


class MissionOrigin(StrEnum):
    THE_CREATION = "THE_CREATION"
    PROTO_INTERNAL = "PROTO_INTERNAL"


class MissionPriority(StrEnum):
    LOW = "LOW"
    NORMAL = "NORMAL"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"


class MissionState(StrEnum):
    ACCEPTED = "ACCEPTED"
    REJECTED = "REJECTED"
    BLOCKED = "BLOCKED"
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    DEGRADED = "DEGRADED"


class Mission(BaseModel):
    """Versioned, side-effect-constrained request accepted by ProtoBrain.

    This contract is the PROTO side of a future The Creation bridge. It does not
    imply that a The Creation endpoint or authenticated transport is connected.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False)

    schema_version: str = "1"
    mission_id: UUID
    origin: MissionOrigin
    objective: str = Field(min_length=1, max_length=2_000)
    requested_jobs: tuple[str, ...] = Field(min_length=1, max_length=32)
    execution_mode: str
    priority: MissionPriority = MissionPriority.NORMAL
    scope: tuple[str, ...] = Field(default=(), max_length=500)
    constraints: dict[str, Any] = Field(default_factory=dict)
    deadline: datetime | None = None

    @field_validator("execution_mode")
    @classmethod
    def validate_safe_mode(cls, value: str) -> str:
        normalized = value.strip().upper()
        if normalized not in SAFE_MODES:
            raise ValueError("mission execution_mode must be a safe PROTO mode")
        return normalized

    @field_validator("requested_jobs")
    @classmethod
    def validate_job_names(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        normalized = tuple(item.strip() for item in value)
        if any(not item for item in normalized):
            raise ValueError("requested_jobs must contain non-empty job names")
        if len(set(normalized)) != len(normalized):
            raise ValueError("requested_jobs must not contain duplicates")
        return normalized

    @model_validator(mode="after")
    def validate_deadline(self) -> Mission:
        if self.deadline is not None and (
            self.deadline.tzinfo is None or self.deadline.utcoffset() is None
        ):
            raise ValueError("deadline must be timezone-aware")
        return self


class MissionReceipt(BaseModel):
    model_config = ConfigDict(frozen=True)

    mission_id: UUID
    state: MissionState
    accepted_jobs: tuple[str, ...] = ()
    rejected_reason: str | None = None
    job_run_ids: tuple[str, ...] = ()
    financial_connectivity: bool = False
    real_money_execution: bool = False


class MissionGateway:
    """Maps allowlisted missions to durable ProtoBrain jobs.

    Authentication belongs to the transport boundary. `identity_verified` is
    therefore mandatory here so unauthenticated callers cannot enqueue work by
    accidentally bypassing the future HTTP/service-identity layer.
    """

    def __init__(self, brain: ProtoBrain, allowed_jobs: frozenset[str]) -> None:
        self.brain = brain
        self.allowed_jobs = allowed_jobs

    async def accept(self, mission: Mission, *, identity_verified: bool) -> MissionReceipt:
        if not identity_verified:
            return MissionReceipt(
                mission_id=mission.mission_id,
                state=MissionState.REJECTED,
                rejected_reason="service identity not verified",
            )

        requested = set(mission.requested_jobs)
        outside_allowlist = sorted(requested - self.allowed_jobs)
        if outside_allowlist:
            return MissionReceipt(
                mission_id=mission.mission_id,
                state=MissionState.REJECTED,
                rejected_reason=(
                    "mission requested jobs outside the Creation bridge allowlist: "
                    + ",".join(outside_allowlist)
                ),
            )

        unavailable = sorted(requested - self.brain.specs.keys())
        if unavailable:
            return MissionReceipt(
                mission_id=mission.mission_id,
                state=MissionState.DEGRADED,
                rejected_reason="requested jobs are not registered: " + ",".join(unavailable),
            )

        incompatible = sorted(
            job_name
            for job_name in requested
            if mission.execution_mode not in self.brain.specs[job_name].allowed_modes
        )
        if incompatible:
            return MissionReceipt(
                mission_id=mission.mission_id,
                state=MissionState.BLOCKED,
                rejected_reason=(
                    "mission mode is not permitted for jobs: " + ",".join(incompatible)
                ),
            )

        runs = []
        for job_name in mission.requested_jobs:
            run = await self.brain.enqueue(
                job_name,
                idempotency_key=f"mission:{mission.mission_id}:{job_name}",
                mode=mission.execution_mode,
                payload={
                    "mission_id": str(mission.mission_id),
                    "origin": mission.origin.value,
                    "objective": mission.objective,
                    "scope": list(mission.scope),
                    "constraints": dict(mission.constraints),
                    "priority": mission.priority.value,
                    "deadline": mission.deadline.isoformat() if mission.deadline else None,
                },
            )
            runs.append(run)

        return MissionReceipt(
            mission_id=mission.mission_id,
            state=MissionState.ACCEPTED,
            accepted_jobs=mission.requested_jobs,
            job_run_ids=tuple(run.id for run in runs),
        )
