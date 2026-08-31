from __future__ import annotations

from .models import RuntimeState
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
runtime = RuntimeState()
portfolio = PaperPortfolio()
register_portfolio_recovery_target(portfolio)
replay_session = ReplaySession(on_timeline_reset=portfolio.reset)
simulator = PaperSimulator(reference_time_provider=lambda: replay_session.current_timestamp)


def reset_runtime_state() -> RuntimeState:
    fresh = RuntimeState()
    runtime.mode = fresh.mode
    runtime.running = fresh.running
    runtime.kill_switch = fresh.kill_switch
    runtime.replay_speed = fresh.replay_speed
    runtime.started_at = fresh.started_at
    return runtime


__all__ = [
    "persistence_engine",
    "persistent_journal",
    "portfolio",
    "replay_session",
    "reset_runtime_state",
    "runtime",
    "simulator",
]
