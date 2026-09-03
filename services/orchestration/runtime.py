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

    async def run_once(self, *, now: datetime | None = None) -> JobRun | None:
        current = now or datetime.now(UTC)
        await self.store.recover_stale(self.specs, now=current)
        run = await self.store.claim_next(self.specs, self.worker_id, now=current)
        if run is None:
            return None

        spec = self.specs.get(run.job_name)
        handler = self.handlers.get(run.job_name)
        if spec is None or handler is None:
            return await self.store.dead_letter(
                run.id,
                owner=self.worker_id,
                error="unregistered job",
                now=current,
            )

        if spec.requires_risk_gate:
            if self.risk_gate is None:
                return await self.store.block(
                    run.id,
                    owner=self.worker_id,
                    error="risk gate unavailable",
                    now=current,
                )
            approved = await self.risk_gate(run)
            if not approved:
                return await self.store.block(
                    run.id,
                    owner=self.worker_id,
                    error="risk gate rejected job",
                    now=current,
                )

        try:
            result = await handler(dict(run.payload))
        except Exception as exc:
            return await self.store.fail(
                run.id,
                owner=self.worker_id,
                spec=spec,
                error=f"{type(exc).__name__}: {exc}",
                now=current,
            )

        return await self.store.succeed(
            run.id,
            owner=self.worker_id,
            result=result or {},
            now=current,
        )

    async def heartbeat(self, run_id: str, *, now: datetime | None = None) -> JobRun:
        run = await self.store.get(run_id)
        if run is None:
            raise KeyError(f"unknown job run: {run_id}")
        spec = self.specs.get(run.job_name)
        if spec is None:
            raise KeyError(f"unregistered job: {run.job_name}")
        return await self.store.heartbeat(
            run_id,
            owner=self.worker_id,
            lease_seconds=spec.lease_seconds,
            now=now or datetime.now(UTC),
        )

    @staticmethod
    def next_retry_at(spec: JobSpec, attempts: int, now: datetime) -> datetime:
        exponent = max(attempts - 1, 0)
        return now + timedelta(seconds=spec.base_backoff_seconds * (2**exponent))
