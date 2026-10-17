from __future__ import annotations

from datetime import UTC, datetime

from services.orchestration import DecisionMemoryStore, SqlJobStore

from .models import RuntimeState, SystemMode
from .persistence import (
    AsyncSqlFillJournal,
    build_engine,
    register_portfolio_recovery_target,
)
from .portfolio import PaperPortfolio
from .replay import ReplaySession
from .settings import settings
from .simulation import PaperSimulator

persistence_engine = build_engine(settings.database_url) if settings.persistence_enabled else None
persistent_journal = (
    AsyncSqlFillJournal(persistence_engine) if persistence_engine is not None else None
)
orchestration_engine = (
    persistence_engine
    if persistence_engine is not None
    else build_engine(settings.database_url)
    if settings.orchestration_persistence_active
    else None
)
# Control-plane state (journal, kill switches, lockdown, checkpoints, permits, promotion,
# watchdog coverage, roster). It reuses an engine already open on DATABASE_URL and never
# creates the simulation tables, so it stays durable in production LIVE_MONITORING.
control_engine = (
    persistence_engine
    if persistence_engine is not None
    else orchestration_engine
    if orchestration_engine is not None and settings.control_persistence_active
    else build_engine(settings.database_url)
    if settings.control_persistence_active
    else None
)
orchestration_store = (
    SqlJobStore(orchestration_engine) if orchestration_engine is not None else None
)
decision_memory_store = (
    DecisionMemoryStore(orchestration_engine) if orchestration_engine is not None else None
)
runtime = RuntimeState()

_last_wall_clock = datetime.min.replace(tzinfo=UTC)


def non_decreasing_utc_now() -> datetime:
    """Wall-clock UTC that never steps backwards within the process.

    NTP corrections or a VM resume can move the system clock back. Fill timestamps and
    portfolio as-of times are required to be monotonic, so a backward step would otherwise
    make /v1/simulate and /v1/portfolio fail.
    """
    global _last_wall_clock
    _last_wall_clock = max(datetime.now(UTC), _last_wall_clock)
    return _last_wall_clock


portfolio = PaperPortfolio(
    reference_time_provider=lambda: (
        replay_session.current_timestamp
        if runtime.mode == SystemMode.HISTORICAL_REPLAY
        and replay_session.current_timestamp is not None
        else non_decreasing_utc_now()
    )
)
register_portfolio_recovery_target(portfolio)
replay_session = ReplaySession(on_timeline_reset=portfolio.reset)
simulator = PaperSimulator(
    reference_time_provider=lambda: replay_session.current_timestamp or non_decreasing_utc_now()
)


def reset_runtime_state() -> RuntimeState:
    fresh = RuntimeState()
    runtime.mode = fresh.mode
    runtime.running = fresh.running
    runtime.kill_switch = fresh.kill_switch
    runtime.replay_speed = fresh.replay_speed
    runtime.started_at = fresh.started_at
    return runtime


__all__ = [
    "control_engine",
    "decision_memory_store",
    "orchestration_engine",
    "orchestration_store",
    "persistence_engine",
    "persistent_journal",
    "portfolio",
    "replay_session",
    "reset_runtime_state",
    "runtime",
    "simulator",
]
