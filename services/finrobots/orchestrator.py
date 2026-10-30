"""FinRobot orchestrator: one cycle runs every bound specialist, then the council.

A cycle gathers a read-only snapshot once, gives it to each bound specialist with its own
timeout, keeps only reports that honour the contract (right robot, right cycle), folds them
in the council and keeps a bounded history. A specialist that fails, times out or returns
something else is recorded as an error and contributes nothing. Each completed cycle is
handed to ``on_cycle`` (the API journals it). Cycles run on PROTO's own schedule, never on a
ChatGPT session: ChatGPT can only read their results.
"""

from __future__ import annotations

import asyncio
import logging
from collections import deque
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

from pydantic import ValidationError

from .contracts import FinRobotId, FinRobotReport, Stance
from .council import CouncilResult, decide
from .registry import CycleContext, FinRobotRegistry

logger = logging.getLogger("proto.finrobots")

SnapshotProvider = Callable[[], Awaitable[Mapping[str, Any]]]
CycleSink = Callable[["CycleRecord"], Awaitable[None]]

HISTORY_CAPACITY = 50


@dataclass(frozen=True, slots=True)
class CycleRecord:
    cycle_id: UUID
    started_at: datetime
    finished_at: datetime
    instrument_ids: tuple[str, ...]
    reports: tuple[FinRobotReport, ...]
    errors: dict[str, str]
    council: CouncilResult
    snapshot_sources: tuple[str, ...]
    # Per-source status, read time and provenance, as the snapshot's ``_sources`` reported them.
    sources: Mapping[str, Mapping[str, Any]] = field(default_factory=dict)

    def summary(self) -> dict[str, object]:
        return {
            "cycle_id": str(self.cycle_id),
            "started_at": self.started_at.isoformat(),
            "finished_at": self.finished_at.isoformat(),
            "instrument_ids": list(self.instrument_ids),
            "reports": len(self.reports),
            "errors": dict(self.errors),
            "verdicts": {s.scope: s.verdict.value for s in self.council.scopes},
            "snapshot_sources": list(self.snapshot_sources),
            "sources": {k: dict(v) for k, v in self.sources.items()},
        }

    def to_dict(self) -> dict[str, object]:
        return {
            **self.summary(),
            "reports": [r.model_dump(mode="json") for r in self.reports],
            "council": self.council.to_dict(),
        }


_STANCE_ORDER = {Stance.VETO: 0, Stance.CAUTION: 1, Stance.SUPPORT: 2, Stance.ABSTAIN: 3}


def _restrictiveness(item: object) -> int:
    if isinstance(item, FinRobotReport):
        return _STANCE_ORDER[item.stance]
    return len(_STANCE_ORDER)  # unvalidated items go last


def _source_meta(snapshot: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    meta = snapshot.get("_sources")
    sources = meta.get("sources") if isinstance(meta, Mapping) else None
    if not isinstance(sources, Mapping):
        return {}
    keep = ("status", "read_at", "provenance", "error")
    return {
        str(name): {k: value.get(k) for k in keep}
        for name, value in sources.items()
        if isinstance(value, Mapping)
    }


def _as_reports(raw: object) -> list[object]:
    if isinstance(raw, list | tuple):
        return list(raw)
    return [raw]


class FinRobotOrchestrator:
    def __init__(
        self,
        registry: FinRobotRegistry,
        *,
        snapshot: SnapshotProvider,
        instruments: Callable[[], tuple[str, ...]],
        on_cycle: CycleSink | None = None,
        robot_timeout: timedelta = timedelta(seconds=10),
        max_reports_per_robot: int = 16,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self.registry = registry
        self._snapshot = snapshot
        self._instruments = instruments
        self._on_cycle = on_cycle
        self._timeout = robot_timeout.total_seconds()
        self._max_reports = max_reports_per_robot
        self._clock = clock
        self._history: deque[CycleRecord] = deque(maxlen=HISTORY_CAPACITY)
        self._lock = asyncio.Lock()
        self._task: asyncio.Task[None] | None = None
        self.sink_failures = 0

    @property
    def history(self) -> tuple[CycleRecord, ...]:
        return tuple(self._history)

    def latest(self) -> CycleRecord | None:
        return self._history[-1] if self._history else None

    async def _run_robot(
        self, robot, context: CycleContext
    ) -> tuple[list[FinRobotReport], str | None]:
        robot_id = FinRobotId(robot.robot_id)
        try:
            raw = await asyncio.wait_for(robot.analyze(context), timeout=self._timeout)
        except TimeoutError:
            return [], "timed out"
        except Exception as exc:  # a specialist's failure never breaks the cycle
            logger.warning("finrobot %s failed", robot_id.value, exc_info=True)
            return [], f"failed: {type(exc).__name__}"
        reports: list[FinRobotReport] = []
        problems: list[str] = []
        items = _as_reports(raw)
        # One report per instrument plus one SYSTEM report must always fit, so a per-instrument
        # veto is never cut by the cap; the cap only bounds a robot that floods the cycle.
        limit = max(self._max_reports, len(context.instrument_ids) + 1)
        if len(items) > limit:
            # Keep the most restrictive views when something has to go.
            items.sort(key=_restrictiveness)
            problems.append(f"{len(items) - limit} report(s) over the limit of {limit} dropped")
            items = items[:limit]
        for item in items:
            if isinstance(item, dict):
                try:
                    item = FinRobotReport.model_validate(item)
                except ValidationError:
                    problems.append("invalid report")
                    continue
            if not isinstance(item, FinRobotReport):
                problems.append("not a FinRobotReport")
            elif item.robot_id is not robot_id:
                problems.append("report signed as another robot")
            elif item.cycle_id != context.cycle_id:
                problems.append("report from another cycle")
            else:
                reports.append(item)
        return reports, ("; ".join(sorted(set(problems))) or None)

    async def run_cycle(self) -> CycleRecord:
        async with self._lock:
            started = self._clock()
            cycle_id = uuid4()
            try:
                snapshot = dict(await self._snapshot())
            except Exception:
                logger.warning("finrobot snapshot failed", exc_info=True)
                snapshot = {}
            context = CycleContext(
                cycle_id=cycle_id,
                started_at=started,
                instrument_ids=tuple(self._instruments()),
                snapshot=snapshot,
            )
            robots = self.registry.bound()
            results = await asyncio.gather(*(self._run_robot(r, context) for r in robots))
            reports: list[FinRobotReport] = []
            errors: dict[str, str] = {}
            for robot, (found, error) in zip(robots, results, strict=True):
                reports.extend(found)
                if error:
                    errors[FinRobotId(robot.robot_id).value] = error
            finished = self._clock()
            record = CycleRecord(
                cycle_id=cycle_id,
                started_at=started,
                finished_at=finished,
                instrument_ids=context.instrument_ids,
                reports=tuple(reports),
                errors=errors,
                council=decide(reports, now=finished),
                snapshot_sources=tuple(sorted(k for k in snapshot if not k.startswith("_"))),
                sources=_source_meta(snapshot),
            )
            self._history.append(record)
        if self._on_cycle is not None:
            try:
                await self._on_cycle(record)
            except Exception:
                self.sink_failures += 1
                logger.warning("finrobot cycle sink failed", exc_info=True)
        return record

    # -- continuous operation -------------------------------------------------------------

    @property
    def running(self) -> bool:
        return self._task is not None and not self._task.done()

    def start(self, interval: timedelta) -> None:
        if self.running:
            return
        seconds = max(5.0, interval.total_seconds())

        async def loop() -> None:
            while True:
                try:
                    await self.run_cycle()
                except Exception:
                    logger.exception("finrobot cycle crashed")
                await asyncio.sleep(seconds)

        self._task = asyncio.create_task(loop(), name="finrobot-cycles")

    async def stop(self) -> None:
        task, self._task = self._task, None
        if task is not None:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
