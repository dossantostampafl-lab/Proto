"""FinRobots surface: registry, cycle history and council verdicts (docs/finrobots/contract.md).

Read routes: GET /v1/finrobots, /v1/finrobots/cycles, /v1/finrobots/council/latest.
POST /v1/finrobots/cycles runs one cycle now (operator token, like every mutating route); a
cycle only reads and journals, it can never place, size or permit an order.

Specialists are bound from ``services.finrobots.robots.default_robots()`` when that module
exists (the engines' branch provides it); until then every specialist is ``UNBOUND`` and a
cycle truthfully has no reports.
"""

from __future__ import annotations

import asyncio
import importlib
import inspect
import logging
from collections.abc import Callable, Mapping
from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import APIRouter, HTTPException

from services.finrobots import CONTRACT_VERSION, CycleRecord, FinRobotOrchestrator, FinRobotRegistry

from . import oms_surface
from .agent_surface import agent_status, live_quote, recent_decisions
from .event_state import JournalUnavailable, record_operational_event
from .instrument_registry import active_instruments
from .risk_surface import risk_status
from .settings import settings
from .watchdog_surface import watchdog_status

logger = logging.getLogger("proto.finrobots")

router = APIRouter(prefix="/v1/finrobots", tags=["finrobots"])

_BOUNDARY = {"financial_connectivity": False, "real_money_execution": False}
_ROBOTS_MODULE = "services.finrobots.robots"

_orchestrator: FinRobotOrchestrator | None = None
journal_failures = 0


SNAPSHOT_VERSION = "finrobots-snapshot/1.4"
_SOURCE_TIMEOUT_SECONDS = 3.0


def _quotes() -> dict[str, Any]:
    """Live public quotes of the active instruments; ``None`` when PROTO has none."""

    now = datetime.now(UTC)
    out: dict[str, Any] = {}
    for instrument_id in sorted(active_instruments()):
        quote = live_quote(instrument_id)
        if quote is None:
            out[instrument_id] = None
            continue
        observed = quote.observed_at.astimezone(UTC)
        out[instrument_id] = {
            "bid": str(quote.bid),
            "ask": str(quote.ask),
            "observed_at": observed.isoformat(),
            "age_seconds": round((now - observed).total_seconds(), 3),
        }
    return out


class SourceNotReady(RuntimeError):
    """A source that could only be read by starting or changing something."""


async def _orders() -> dict[str, Any]:
    # In paper mode the OMS starts lazily (opening balances, order recovery); a FinRobot cycle
    # must not be what starts it, so an OMS that is not up yet is simply unavailable.
    if not oms_surface.executor_mode() and not oms_surface.paper_oms_started():
        raise SourceNotReady("paper OMS not started")
    return await oms_surface.list_orders(limit=50)


async def _balances() -> dict[str, Any]:
    try:
        return await oms_surface.pure_balances()
    except LookupError as exc:
        raise SourceNotReady(str(exc)) from exc


_QUANT_EVIDENCE_MODULE = "services.quant.evidence"


async def _quant_evidence() -> dict[str, Any]:
    """Recent ``quant-evidence/1`` records (docs/finrobots/contract.md): a pure SELECT on the
    research store. Unavailable when that contract is not installed or there is no store (the
    production LIVE_MONITORING API has none: research data never goes to the control plane)."""

    try:
        module = importlib.import_module(_QUANT_EVIDENCE_MODULE)
    except ModuleNotFoundError as exc:
        if exc.name != _QUANT_EVIDENCE_MODULE:
            raise
        raise SourceNotReady("quant-evidence/1 is not installed") from exc
    from .app_state import persistence_engine

    try:
        records = await module.recent_quant_evidence(persistence_engine, limit=20)
    except LookupError as exc:
        raise SourceNotReady(str(exc)) from exc
    return {"records": records, "contract_version": "quant-evidence/1"}


_QUANT_PROVENANCE = {"SIMULATED", "REPLAY", "PAPER"}


def _quant_provenance(value: Mapping[str, Any]) -> str:
    """The records' own input provenance; never LIVE, and UNAVAILABLE with no record."""

    kinds = {r.get("input_provenance") for r in value.get("records", ()) if isinstance(r, Mapping)}
    if not kinds:
        return "UNAVAILABLE"
    if len(kinds) == 1 and kinds <= _QUANT_PROVENANCE:
        return str(next(iter(kinds)))
    return "MIXED" if kinds <= _QUANT_PROVENANCE else "UNAVAILABLE"


def _readers() -> dict[str, tuple[Callable[[], Any], str]]:
    """Snapshot keys -> (read-only reader, provenance of its data). Every reader is one of
    PROTO's own GET routes (or a pure read beside one), verified to change nothing: no
    checkpoint, journal, metric, schema or OMS start-up. Routes that do stay out until a pure
    read exists: ``/v1/oms/ledger`` writes equity checkpoints (``oms.balances`` reads the same
    balances without it), reconciliation counts metrics, the promotion registry creates its
    table on first read and the roster syncs fills.

    Readers in ``IN_MEMORY_SOURCES`` are synchronous reads of process state (no I/O) and run on
    the event loop without a timeout: moving them to a thread would race with the state the loop
    mutates, and a thread timeout would not stop the work anyway. Every other reader is a
    coroutine (it may reach the executor over HTTP) and is bounded by the 3 s timeout.
    """

    mode = data_mode()
    return {
        "agents.status": (agent_status, mode),
        "agents.decisions_recent": (lambda: recent_decisions(limit=50), mode),
        "risk.status": (risk_status, mode),
        "market.quotes": (_quotes, "LIVE"),
        "oms.orders": (_orders, mode),
        "oms.balances": (_balances, mode),
        "oms.executor": (oms_surface.executor_status, mode),
        "watchdog.status": (watchdog_status, mode),
        # Provenance is replaced by the records' own (see read_snapshot).
        "quant.evidence": (_quant_evidence, "UNAVAILABLE"),
    }


IN_MEMORY_SOURCES = frozenset(
    {"agents.status", "agents.decisions_recent", "risk.status", "market.quotes", "watchdog.status"}
)


async def _read(read: Callable[[], Any]) -> Any:
    value = read()
    if inspect.isawaitable(value):
        value = await asyncio.wait_for(value, timeout=_SOURCE_TIMEOUT_SECONDS)
    return value


async def read_snapshot() -> Mapping[str, Any]:
    """The cycle's evidence, read once from PROTO's own read-only routes.

    ``_sources`` says, per key, whether it was read (``OK``), failed (``UNAVAILABLE``) or timed
    out (``TIMEOUT``), when, and the provenance of its data. A source that could not be read is
    absent from the snapshot, never invented.
    """

    snapshot: dict[str, Any] = {}
    sources: dict[str, dict[str, Any]] = {}
    for name, (read, provenance) in _readers().items():
        read_at = datetime.now(UTC).isoformat()
        try:
            snapshot[name] = await _read(read)
            status, error = "OK", None
        except TimeoutError:
            status, error = "TIMEOUT", None
        except SourceNotReady as exc:
            # An expected state (OMS not started, store absent): no traceback every cycle.
            logger.debug("finrobot snapshot source %s not ready: %s", name, exc)
            status, error = "UNAVAILABLE", "SourceNotReady"
        except Exception as exc:
            logger.warning("finrobot snapshot source %s unavailable", name, exc_info=True)
            status, error = "UNAVAILABLE", type(exc).__name__
        if status != "OK" or (name == "market.quotes" and not any(snapshot[name].values())):
            # Read but empty (no live quote at all) is not live data.
            provenance = "UNAVAILABLE"
        elif name == "quant.evidence":
            provenance = _quant_provenance(snapshot[name])
        sources[name] = {
            "status": status,
            "read_at": read_at,
            "provenance": provenance,
            "error": error,
        }
    snapshot["_sources"] = {"version": SNAPSHOT_VERSION, "sources": sources}
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


def council_brief(instrument_id: str, *, max_reports: int = 12) -> dict[str, object]:
    """A compact, advisory view of the latest cycle for one instrument (and the SYSTEM scope),
    for the ChatGPT explanation chat. Never an order: it says what the specialists saw."""

    latest = orchestrator().latest()
    if latest is None:
        return {"available": False, "reason": "no FinRobot cycle has run", **_BOUNDARY}
    scopes = {instrument_id, "SYSTEM"}
    verdicts = [
        {"scope": v.scope, "verdict": v.verdict.value, "reasons": list(v.reasons[:5])}
        for v in latest.council.scopes
        if v.scope in scopes
    ]
    reports = [
        {
            "robot_id": r.robot_id.value,
            "scope": r.instrument_id or "SYSTEM",
            "stance": r.stance.value,
            "confidence": r.confidence,
            "headline": r.headline,
        }
        for r in latest.reports
        if (r.instrument_id or "SYSTEM") in scopes
    ][:max_reports]
    return {
        "available": True,
        "cycle_id": str(latest.cycle_id),
        "finished_at": latest.finished_at.isoformat(),
        "data_mode": data_mode(),
        "verdicts": verdicts,
        "reports": reports,
        "advisory_only": True,
        "order_authority": False,
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
