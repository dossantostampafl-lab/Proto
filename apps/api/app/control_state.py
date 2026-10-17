"""Durable control-plane store: what must survive a deploy for the limits to stay honest.

Production runs LIVE_MONITORING, where PERSISTENCE_ENABLED (simulation persistence) is refused.
The control plane does not need the simulation tables: it needs the operational journal, the
kill switches and lockdown latch, the start-of-day and peak equity checkpoints, spent permits,
the promotion records, watchdog coverage and the agent roster. Those live on ``control_engine``
(``settings.control_persistence_active``), which shares DATABASE_URL with orchestration.

Fail closed: when the store is configured but cannot be reached, the status is UNAVAILABLE and
the Risk Engine refuses every new proposal (``accepts_new_risk``). It never falls back to memory.
A later call retries at most every ``RETRY_INTERVAL`` and resumes once the database answers.
"""

from __future__ import annotations

import logging
from enum import StrEnum
from time import monotonic

from sqlalchemy.ext.asyncio import AsyncEngine

from .app_state import control_engine
from .event_state import operational_journal
from .persistence import init_control_database
from .settings import settings

logger = logging.getLogger("proto.control")

RETRY_INTERVAL = 15.0  # seconds between reconnection attempts while UNAVAILABLE

DURABLE_STORES = (
    "operational_journal",
    "risk_controls",
    "equity_checkpoints",
    "permit_ledger",
    "paper_oms_orders_and_ledger",
    "promotion_registry",
    "watchdog_coverage",
    "agent_roster",
)


class ControlStatus(StrEnum):
    MEMORY = "MEMORY"  # no durable store configured: state lasts as long as the process
    PENDING = "PENDING"  # configured, not reached yet
    DURABLE = "DURABLE"
    UNAVAILABLE = "UNAVAILABLE"  # configured but unreachable: new risk is refused


class ControlPlaneStore:
    def __init__(self, engine: AsyncEngine | None, *, process_local: bool = False) -> None:
        self.engine = engine
        self.process_local = process_local
        self.status = ControlStatus.MEMORY if engine is None else ControlStatus.PENDING
        self.error: str | None = None
        # Increments on every successful (re)connection, so readers know to reload.
        self.generation = 0
        self._last_attempt: float | None = None

    @property
    def configured(self) -> bool:
        return self.engine is not None

    @property
    def durable(self) -> bool:
        return self.status is ControlStatus.DURABLE and not self.process_local

    def accepts_new_risk(self) -> bool:
        return self.status in {ControlStatus.MEMORY, ControlStatus.DURABLE}

    async def start(self) -> bool:
        """Create the control-plane tables and restore the journal head. False: unreachable."""

        if self.engine is None:
            self.status = ControlStatus.MEMORY
            await operational_journal.start()
            return True
        self._last_attempt = monotonic()
        try:
            await init_control_database(self.engine)
            await operational_journal.start()
        except Exception as exc:  # any driver error: the store is not usable
            self.mark_unavailable(exc)
            return False
        self.status = ControlStatus.DURABLE
        self.error = None
        self.generation += 1
        return True

    async def ensure(self) -> bool:
        """True when state can be read and written (or no store is configured)."""

        if self.status in {ControlStatus.MEMORY, ControlStatus.DURABLE}:
            return True
        if (
            self.status is ControlStatus.UNAVAILABLE
            and self._last_attempt is not None
            and monotonic() - self._last_attempt < RETRY_INTERVAL
        ):
            return False
        return await self.start()

    def mark_unavailable(self, exc: BaseException) -> None:
        if self.engine is None:
            return
        if self.status is not ControlStatus.UNAVAILABLE:
            logger.critical("durable control store unavailable, refusing new risk: %r", exc)
        self.status = ControlStatus.UNAVAILABLE
        self.error = type(exc).__name__
        if self._last_attempt is None:
            self._last_attempt = monotonic()

    def snapshot(self) -> dict[str, object]:
        return {
            "configured": self.configured,
            "status": self.status.value,
            "durable": self.durable,
            "scope": (
                "durable_database"
                if self.durable
                else "process_memory"
                if self.status is ControlStatus.MEMORY or self.process_local
                else "unavailable"
            ),
            "accepts_new_risk": self.accepts_new_risk(),
            "database_driver": settings.database_driver if self.configured else None,
            "stores": list(DURABLE_STORES) if self.configured else [],
            "error": self.error,
            "general_simulation_persistence_enabled": settings.persistence_enabled,
        }


control_store = ControlPlaneStore(
    control_engine,
    process_local=control_engine is not None and settings.database_process_local,
)


def configure_control_store(engine: AsyncEngine | None) -> ControlPlaneStore:
    """Point the control plane at another engine (tests: a restart against the same file)."""

    from services.events import SqlJournalStore

    from .event_state import JournalIntegrity

    global control_store
    control_store = ControlPlaneStore(engine)
    operational_journal.store = SqlJournalStore(engine) if engine is not None else None
    operational_journal.journal.reset()
    operational_journal.integrity = (
        JournalIntegrity.UNVERIFIED if engine is not None else JournalIntegrity.VALID
    )
    operational_journal.corruption = None
    return control_store


def current() -> ControlPlaneStore:
    return control_store


__all__ = [
    "DURABLE_STORES",
    "ControlPlaneStore",
    "ControlStatus",
    "configure_control_store",
    "control_store",
    "current",
]
