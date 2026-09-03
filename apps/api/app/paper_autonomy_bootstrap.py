from __future__ import annotations

import os
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import AsyncIterator, Mapping

from fastapi import APIRouter

from .app_state import replay_session, runtime
from .models import KillSwitchState, SystemMode
from .paper_autopilot import PaperAutopilotConfig, paper_autopilot

_ENV_PREFIX = "PROTO_PAPER_AUTONOMY_"
_REQUIRED_ENV = (
    "SYMBOL",
    "IMBALANCE_TRIGGER",
    "COOLDOWN_SECONDS",
    "QUANTITY",
    "MAX_SPREAD_BPS",
    "STOP_LOSS_FRACTION",
)


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
            ),
            "autopilot_running": paper_autopilot.running,
            "financial_connectivity": False,
            "real_money_execution": False,
        }


bootstrap_state = PaperAutonomyBootstrapState()


async def start_configured_paper_autonomy(
    env: Mapping[str, str] | None = None,
) -> dict[str, object]:
    bootstrap_state.enabled = _enabled(
        (env if env is not None else os.environ).get(f"{_ENV_PREFIX}ENABLED")
    )
    bootstrap_state.configured = False
    bootstrap_state.started = False
    bootstrap_state.config = None

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


async def stop_configured_paper_autonomy() -> None:
    if bootstrap_state.started and paper_autopilot.running:
        await paper_autopilot.stop()
    if bootstrap_state.started and runtime.mode == SystemMode.PAPER_TRADING:
        runtime.running = False
    bootstrap_state.started = False


@asynccontextmanager
async def bootstrap_lifespan(_: APIRouter) -> AsyncIterator[None]:
    await start_configured_paper_autonomy()
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
    "router",
    "start_configured_paper_autonomy",
    "stop_configured_paper_autonomy",
]
