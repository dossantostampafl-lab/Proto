"""FinRobots surface: registry, cycle history and council verdicts (docs/finrobots/contract.md).

Read routes: GET /v1/finrobots, /v1/finrobots/cycles, /v1/finrobots/council/latest.
POST /v1/finrobots/cycles runs one cycle now (operator token, like every mutating route); a
cycle only reads and journals, it can never place, size or permit an order.

Specialists are bound from ``services.finrobots.robots.default_robots()`` when that module
exists (the engines' branch provides it); until then every specialist is ``UNBOUND`` and a
cycle truthfully has no reports.
"""

from __future__ import annotations

import importlib
import logging
from collections.abc import Mapping
from datetime import timedelta
from typing import Any

from fastapi import APIRouter, HTTPException

from services.finrobots import CONTRACT_VERSION, CycleRecord, FinRobotOrchestrator, FinRobotRegistry

from .agent_surface import agent_status, recent_decisions
from .event_state import JournalUnavailable, record_operational_event
from .instrument_registry import active_instruments
from .risk_surface import risk_status
from .settings import settings

logger = logging.getLogger("proto.finrobots")

router = APIRouter(prefix="/v1/finrobots", tags=["finrobots"])

_BOUNDARY = {"financial_connectivity": False, "real_money_execution": False}
_ROBOTS_MODULE = "services.finrobots.robots"

_orchestrator: FinRobotOrchestrator | None = None
journal_failures = 0


async def read_snapshot() -> Mapping[str, Any]:
    """The cycle's evidence, read once from PROTO's own read-only routes."""

    snapshot: dict[str, Any] = {}
    readers = {
        "agents.status": agent_status,
        "agents.decisions_recent": lambda: recent_decisions(limit=50),
        "risk.status": risk_status,
    }
    for name, read in readers.items():
        try:
            snapshot[name] = read()
        except Exception:  # an unavailable source is absent, never invented
            logger.warning("finrobot snapshot source %s unavailable", name, exc_info=True)
    return snapshot


async def _journal_cycle(record: CycleRecord) -> None:
    global journal_failures
    try:
        await record_operational_event(
            event_type="FINROBOT_CYCLE_COMPLETED",
            source="finrobots",
            payload={**record.summary(), **_BOUNDARY},
            idempotency_key=f"finrobot-cycle:{record.cycle_id}",
        )
    except JournalUnavailable:
        journal_failures += 1
        logger.warning("finrobot cycle %s was not journaled", record.cycle_id)


def bind_default_robots(registry: FinRobotRegistry) -> list[str]:
    try:
        module = importlib.import_module(_ROBOTS_MODULE)
    except ModuleNotFoundError as exc:
        if exc.name != _ROBOTS_MODULE:
            raise
        return []
    bound: list[str] = []
    for robot in module.default_robots():
        registry.bind(robot)
        bound.append(str(robot.robot_id))
    return bound


def orchestrator() -> FinRobotOrchestrator:
    global _orchestrator
    if _orchestrator is None:
        registry = FinRobotRegistry()
        bind_default_robots(registry)
        _orchestrator = FinRobotOrchestrator(
            registry,
            snapshot=read_snapshot,
            instruments=lambda: tuple(sorted(active_instruments())),
            on_cycle=_journal_cycle,
        )
    return _orchestrator


def reset_finrobot_state() -> None:
    global _orchestrator, journal_failures
    _orchestrator = None
    journal_failures = 0


async def start_if_enabled() -> None:
    if settings.finrobots_enabled:
        orchestrator().start(timedelta(seconds=settings.finrobots_interval_seconds))


async def stop() -> None:
    if _orchestrator is not None:
        await _orchestrator.stop()


def registry_view() -> dict[str, object]:
    orch = orchestrator()
    robots = orch.registry.describe()
    latest = orch.latest()
    return {
        "contract_version": CONTRACT_VERSION,
        "enabled": settings.finrobots_enabled,
        "running": orch.running,
        "interval_seconds": settings.finrobots_interval_seconds,
        "robots": robots,
        "bound": sum(1 for r in robots if r["status"] == "BOUND"),
        "cycles": len(orch.history),
        "last_cycle": latest.summary() if latest is not None else None,
        "journal_failures": journal_failures,
        "authority": "ADVISORY_ONLY",
        **_BOUNDARY,
    }


def cycles_view(limit: int = 10) -> dict[str, object]:
    history = orchestrator().history
    selected = history[-max(1, min(limit, len(history) or 1)) :] if history else ()
    return {"cycles": [c.to_dict() for c in reversed(selected)], **_BOUNDARY}


def data_mode() -> str:
    return "TESTNET" if settings.oms_execution_backend == "executor" else "PAPER"


def council_view() -> dict[str, object]:
    latest = orchestrator().latest()
    if latest is None:
        return {"council": None, "cycle_id": None, "data_mode": "UNAVAILABLE", **_BOUNDARY}
    return {
        "council": latest.council.to_dict(),
        "cycle_id": str(latest.cycle_id),
        "data_mode": data_mode(),
        **_BOUNDARY,
    }


@router.get("")
def finrobots() -> dict[str, object]:
    return registry_view()


@router.get("/cycles")
def finrobot_cycles(limit: int = 10) -> dict[str, object]:
    return cycles_view(limit)


@router.get("/council/latest")
def finrobot_council() -> dict[str, object]:
    return council_view()


@router.post("/cycles")
async def run_finrobot_cycle() -> dict[str, object]:
    orch = orchestrator()
    if not settings.finrobots_enabled:
        raise HTTPException(status_code=409, detail="FinRobots are disabled (FINROBOTS_ENABLED)")
    record = await orch.run_cycle()
    return {"cycle": record.to_dict(), **_BOUNDARY}
