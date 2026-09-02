from __future__ import annotations

import asyncio
from contextlib import suppress
from dataclasses import dataclass
from datetime import UTC, datetime

from .runtime import ProtoBrain


@dataclass(frozen=True, slots=True)
class PeriodicJob:
    job_name: str
    interval_seconds: int
    mode: str

    def __post_init__(self) -> None:
        if self.interval_seconds < 1:
            raise ValueError("interval_seconds must be >= 1")

    def idempotency_key(self, now: datetime) -> str:
        timestamp = int(now.timestamp())
        bucket = timestamp // self.interval_seconds
        return f"periodic:{self.job_name}:{self.mode}:{self.interval_seconds}:{bucket}"


class OrchestrationSupervisor:
    def __init__(
        self,
        brain: ProtoBrain,
        schedules: tuple[PeriodicJob, ...],
        *,
        poll_seconds: float = 1.0,
    ) -> None:
        if poll_seconds <= 0:
            raise ValueError("poll_seconds must be > 0")
        self.brain = brain
        self.schedules = schedules
        self.poll_seconds = poll_seconds
        self._task: asyncio.Task[None] | None = None
        self._last_error: str | None = None
        self._ticks = 0

    @property
    def running(self) -> bool:
        return self._task is not None and not self._task.done()

    def status(self) -> dict[str, object]:
        return {
            "running": self.running,
            "worker_id": self.brain.worker_id,
            "registered_jobs": sorted(self.brain.specs),
            "scheduled_jobs": [schedule.job_name for schedule in self.schedules],
            "ticks": self._ticks,
            "last_error": self._last_error,
            "financial_connectivity": False,
            "real_money_execution": False,
        }

    async def tick(self, *, now: datetime | None = None) -> None:
        current = now or datetime.now(UTC)
        for schedule in self.schedules:
            await self.brain.enqueue(
                schedule.job_name,
                idempotency_key=schedule.idempotency_key(current),
                mode=schedule.mode,
                payload={
                    "scheduled_at": current.isoformat(),
                    "schedule_interval_seconds": schedule.interval_seconds,
                },
            )
        while await self.brain.run_once(now=current) is not None:
            pass
        self._ticks += 1
        self._last_error = None

    async def start(self) -> None:
        if self.running:
            return
        self._task = asyncio.create_task(self._run(), name="proto-brain-supervisor")

    async def stop(self) -> None:
        task = self._task
        if task is None:
            return
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task
        self._task = None

    async def _run(self) -> None:
        while True:
            try:
                await self.tick()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self._last_error = f"{type(exc).__name__}: {exc}"
            await asyncio.sleep(self.poll_seconds)
