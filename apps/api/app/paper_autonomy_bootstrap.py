from __future__ import annotations

import asyncio
import os
from collections.abc import AsyncIterator, Mapping
from contextlib import asynccontextmanager, suppress
from dataclasses import dataclass
from datetime import UTC, datetime

from fastapi import APIRouter

from .app_state import replay_session, runtime
from .models import KillSwitchState, SystemMode
from .paper_autopilot import PaperAutopilotConfig, paper_autopilot
from .risk_state import simulation_execution_allowed

_ENV_PREFIX = "PROTO_PAPER_AUTONOMY_"
_REQUIRED_ENV = (
    "SYMBOL",
    "IMBALANCE_TRIGGER",
    "COOLDOWN_SECONDS",
    "QUANTITY",
    "MAX_SPREAD_BPS",
    "STOP_LOSS_FRACTION",
)
_WATCHDOG_SECONDS = 5.0


def _enabled(value: str | None) -> bool:
    return (value or "").strip().lower() in {"1", "true", "yes", "on"}


def load_bootstrap_config(
    env: Mapping[str, str] | None = None,
) -> PaperAutopilotConfig | None:
    source = env if env is not None else os.environ
    if not _enabled(source.get(f"{_ENV_PREFIX}ENABLED")):
        return None

    missing = [name for name in _REQUIRED_ENV if not source.get(f"{_ENV_PREFIX}{name}", "").strip()]
    if missing:
        raise ValueError(
            "autonomous paper startup is enabled but required configuration is missing: "
            + ",".join(missing)
        )

    return PaperAutopilotConfig.model_validate(
        {
            "symbol": source[f"{_ENV_PREFIX}SYMBOL"],
            "imbalance_trigger": source[f"{_ENV_PREFIX}IMBALANCE_TRIGGER"],
            "cooldown_seconds": source[f"{_ENV_PREFIX}COOLDOWN_SECONDS"],
            "quantity": source[f"{_ENV_PREFIX}QUANTITY"],
            "max_spread_bps": source[f"{_ENV_PREFIX}MAX_SPREAD_BPS"],
            "stop_loss_fraction": source[f"{_ENV_PREFIX}STOP_LOSS_FRACTION"],
        }
    )


@dataclass(slots=True)
class PaperAutonomyBootstrapState:
    enabled: bool = False
    configured: bool = False
    started: bool = False
    last_reason: str = "DISABLED"
    config: PaperAutopilotConfig | None = None
    watchdog_running: bool = False
    watchdog_checks: int = 0
    watchdog_restarts: int = 0
    watchdog_failures: int = 0
    watchdog_last_check_at: datetime | None = None
    watchdog_last_error: str | None = None

    def snapshot(self) -> dict[str, object]:
        return {
            "enabled": self.enabled,
            "configured": self.configured,
            "started": self.started,
            "last_reason": self.last_reason,
            "config": self.config.model_dump(mode="json") if self.config is not None else None,
            "paper_runtime_ready": bool(
                runtime.mode == SystemMode.PAPER_TRADING
                and runtime.running
                and runtime.kill_switch == KillSwitchState.ARMED
                and simulation_execution_allowed()
            ),
            "autopilot_running": paper_autopilot.running,
            "watchdog": {
                "running": self.watchdog_running,
                "checks": self.watchdog_checks,
                "restarts": self.watchdog_restarts,
                "failures": self.watchdog_failures,
                "last_check_at": (
                    self.watchdog_last_check_at.isoformat()
                    if self.watchdog_last_check_at is not None
                    else None
                ),
                "last_error": self.watchdog_last_error,
            },
            "financial_connectivity": False,
            "real_money_execution": False,
        }


bootstrap_state = PaperAutonomyBootstrapState()
_watchdog_task: asyncio.Task[None] | None = None


async def start_configured_paper_autonomy(
    env: Mapping[str, str] | None = None,
) -> dict[str, object]:
    bootstrap_state.enabled = _enabled(
        (env if env is not None else os.environ).get(f"{_ENV_PREFIX}ENABLED")
    )
    bootstrap_state.configured = False
    bootstrap_state.started = False
    bootstrap_state.config = None
    bootstrap_state.watchdog_restarts = 0
    bootstrap_state.watchdog_failures = 0
    bootstrap_state.watchdog_checks = 0
    bootstrap_state.watchdog_last_check_at = None
    bootstrap_state.watchdog_last_error = None

    try:
        config = load_bootstrap_config(env)
    except (ValueError, TypeError) as error:
        bootstrap_state.last_reason = f"CONFIG_ERROR:{error}"
        return bootstrap_state.snapshot()

    if config is None:
        bootstrap_state.last_reason = "DISABLED"
        return bootstrap_state.snapshot()

    bootstrap_state.configured = True
    bootstrap_state.config = config
    if runtime.kill_switch != KillSwitchState.ARMED:
        bootstrap_state.last_reason = "KILL_SWITCH_NOT_ARMED"
        return bootstrap_state.snapshot()

    replay_session.reset()
    runtime.mode = SystemMode.PAPER_TRADING
    runtime.running = True
    try:
        await paper_autopilot.start(config)
    except Exception as error:
        runtime.running = False
        bootstrap_state.last_reason = f"AUTOPILOT_START_FAILED:{type(error).__name__}"
        return bootstrap_state.snapshot()

    bootstrap_state.started = True
    bootstrap_state.last_reason = "AUTONOMOUS_PAPER_RUNNING"
    return bootstrap_state.snapshot()


async def reconcile_configured_paper_autonomy() -> bool:
    """Restart only a bootstrap-owned worker that died unexpectedly.

    Operator mode changes, a stopped runtime, kill-switch changes and risk-gate
    denial are never overridden. This keeps recovery autonomous without turning a
    watchdog into an execution-policy bypass.
    """
    bootstrap_state.watchdog_checks += 1
    bootstrap_state.watchdog_last_check_at = datetime.now(UTC)

    config = bootstrap_state.config
    if not bootstrap_state.started or config is None:
        return False
    if runtime.mode != SystemMode.PAPER_TRADING or not runtime.running:
        bootstrap_state.last_reason = "WATCHDOG_RUNTIME_NOT_OWNED"
        return False
    if runtime.kill_switch != KillSwitchState.ARMED or not simulation_execution_allowed():
        bootstrap_state.last_reason = "WATCHDOG_SAFETY_GATE_BLOCKED"
        return False
    if paper_autopilot.running:
        bootstrap_state.watchdog_last_error = None
        return False

    try:
        await paper_autopilot.start(config)
    except Exception as error:
        bootstrap_state.watchdog_failures += 1
        bootstrap_state.watchdog_last_error = f"{type(error).__name__}:{error}"
        bootstrap_state.last_reason = "WATCHDOG_RESTART_FAILED"
        return False

    bootstrap_state.watchdog_restarts += 1
    bootstrap_state.watchdog_last_error = None
    bootstrap_state.last_reason = "WATCHDOG_AUTOPILOT_RESTARTED"
    return True


async def _watchdog_loop() -> None:
    bootstrap_state.watchdog_running = True
    try:
        while True:
            await asyncio.sleep(_WATCHDOG_SECONDS)
            await reconcile_configured_paper_autonomy()
    except asyncio.CancelledError:
        raise
    finally:
        bootstrap_state.watchdog_running = False


async def _start_watchdog() -> None:
    global _watchdog_task
    if _watchdog_task is not None and not _watchdog_task.done():
        return
    _watchdog_task = asyncio.create_task(_watchdog_loop(), name="paper-autonomy-watchdog")


async def _stop_watchdog() -> None:
    global _watchdog_task
    task = _watchdog_task
    if task is None:
        return
    task.cancel()
    with suppress(asyncio.CancelledError):
        await task
    _watchdog_task = None
    bootstrap_state.watchdog_running = False


async def stop_configured_paper_autonomy() -> None:
    await _stop_watchdog()
    if bootstrap_state.started and paper_autopilot.running:
        await paper_autopilot.stop()
    if bootstrap_state.started and runtime.mode == SystemMode.PAPER_TRADING:
        runtime.running = False
    bootstrap_state.started = False


@asynccontextmanager
async def bootstrap_lifespan(_: APIRouter) -> AsyncIterator[None]:
    await start_configured_paper_autonomy()
    if bootstrap_state.started:
        await _start_watchdog()
    try:
        yield
    finally:
        await stop_configured_paper_autonomy()


router = APIRouter(
    prefix="/paper/autonomy",
    tags=["paper-autonomy"],
    lifespan=bootstrap_lifespan,
)


@router.get("/status")
def paper_autonomy_status() -> dict[str, object]:
    return bootstrap_state.snapshot()


__all__ = [
    "PaperAutonomyBootstrapState",
    "bootstrap_state",
    "load_bootstrap_config",
    "reconcile_configured_paper_autonomy",
    "router",
    "start_configured_paper_autonomy",
    "stop_configured_paper_autonomy",
]
