from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Any


class JobState(StrEnum):
    QUEUED = "QUEUED"
    RUNNING = "RUNNING"
    RETRY_WAIT = "RETRY_WAIT"
    SUCCEEDED = "SUCCEEDED"
    BLOCKED = "BLOCKED"
    DEAD_LETTER = "DEAD_LETTER"


class JobCapability(StrEnum):
    MARKET_DATA = "MARKET_DATA"
    QUANT_RESEARCH = "QUANT_RESEARCH"
    CALIBRATION = "CALIBRATION"
    RISK = "RISK"
    REPLAY = "REPLAY"
    PORTFOLIO = "PORTFOLIO"
    OBSERVABILITY = "OBSERVABILITY"
    FRONTEND = "FRONTEND"
    PAPER_EXECUTION = "PAPER_EXECUTION"


SAFE_MODES = frozenset(
    {
        "SIMULATION",
        "SHADOW",
        "PAPER_TRADING",
        "HISTORICAL_REPLAY",
        "LIVE_MONITORING",
    }
)


@dataclass(frozen=True, slots=True)
class JobSpec:
    name: str
    capability: JobCapability
    max_attempts: int = 3
    base_backoff_seconds: float = 1.0
    lease_seconds: float = 30.0
    requires_risk_gate: bool = False
    allowed_modes: frozenset[str] = SAFE_MODES
    financial_side_effects: bool = False

    def __post_init__(self) -> None:
        if not self.name.strip():
            raise ValueError("job name must be non-empty")
        if self.max_attempts < 1:
            raise ValueError("max_attempts must be >= 1")
        if self.base_backoff_seconds < 0:
            raise ValueError("base_backoff_seconds must be >= 0")
        if self.lease_seconds <= 0:
            raise ValueError("lease_seconds must be > 0")
        if not self.allowed_modes or not self.allowed_modes.issubset(SAFE_MODES):
            raise ValueError("jobs may only target safe PROTO modes")
        if self.financial_side_effects:
            raise ValueError("real financial side effects are forbidden in ProtoBrain")


@dataclass(slots=True)
class JobRun:
    id: str
    idempotency_key: str
    job_name: str
    capability: JobCapability
    mode: str
    payload: dict[str, Any]
    state: JobState
    attempts: int
    max_attempts: int
    created_at: datetime
    updated_at: datetime
    not_before: datetime
    lease_owner: str | None = None
    lease_expires_at: datetime | None = None
    heartbeat_at: datetime | None = None
    last_error: str | None = None
    result: dict[str, Any] | None = None


Handler = Callable[[dict[str, Any]], Awaitable[dict[str, Any] | None]]
RiskGate = Callable[[JobRun], Awaitable[bool]]


@dataclass(slots=True)
class ProtoBrain:
    store: Any
    worker_id: str
    specs: dict[str, JobSpec] = field(default_factory=dict)
    handlers: dict[str, Handler] = field(default_factory=dict)
    risk_gate: RiskGate | None = None

    def register(self, spec: JobSpec, handler: Handler) -> None:
        if spec.name in self.specs:
            raise ValueError(f"job already registered: {spec.name}")
        self.specs[spec.name] = spec
        self.handlers[spec.name] = handler

    async def enqueue(
        self,
        job_name: str,
        *,
        idempotency_key: str,
        mode: str,
        payload: dict[str, Any] | None = None,
    ) -> JobRun:
        spec = self.specs[job_name]
        if mode not in spec.allowed_modes:
            raise ValueError(f"mode {mode} not allowed for {job_name}")
        if not idempotency_key.strip():
            raise ValueError("idempotency_key must be non-empty")
        return await self.store.enqueue(
            spec,
            idempotency_key=idempotency_key,
            mode=mode,
            payload=payload or {},
        )

    async def run_once(self) -> JobRun | None:
        run = await self.store.claim_next(self.worker_id)
        if run is None:
            return None

        spec = self.specs.get(run.job_name)
        handler = self.handlers.get(run.job_name)
        if spec is None or handler is None:
            await self.store.dead_letter(run.id, "job handler not registered")
            return await self.store.get(run.id)

        if spec.requires_risk_gate:
            if self.risk_gate is None:
                await self.store.block(run.id, "risk gate unavailable")
                return await self.store.get(run.id)
            try:
                approved = await self.risk_gate(run)
            except Exception as error:
                await self.store.block(run.id, f"risk gate error: {type(error).__name__}")
                return await self.store.get(run.id)
            if not approved:
                await self.store.block(run.id, "risk gate rejected")
                return await self.store.get(run.id)

        try:
            result = await handler(dict(run.payload))
        except Exception as error:
            await self.store.fail(
                run.id,
                error=f"{type(error).__name__}: {error}",
                base_backoff_seconds=spec.base_backoff_seconds,
            )
            return await self.store.get(run.id)

        await self.store.succeed(run.id, result or {})
        return await self.store.get(run.id)

    async def heartbeat(self, run_id: str) -> None:
        await self.store.heartbeat(run_id, self.worker_id)

    async def recover_stale(self) -> int:
        return await self.store.recover_stale(datetime.now(UTC))

    async def run_forever(
        self,
        *,
        poll_seconds: float = 0.25,
        stop_when: Callable[[], bool] | None = None,
    ) -> None:
        while stop_when is None or not stop_when():
            run = await self.run_once()
            if run is None:
                import asyncio

                await asyncio.sleep(poll_seconds)


__all__ = [
    "Handler",
    "JobCapability",
    "JobRun",
    "JobSpec",
    "JobState",
    "ProtoBrain",
    "RiskGate",
    "SAFE_MODES",
]
