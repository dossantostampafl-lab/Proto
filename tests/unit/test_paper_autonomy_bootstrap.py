from __future__ import annotations

import pytest

from apps.api.app import paper_autonomy_bootstrap as module
from apps.api.app.app_state import reset_runtime_state, runtime
from apps.api.app.models import KillSwitchState, SystemMode
from apps.api.app.paper_autonomy_bootstrap import load_bootstrap_config


def _enabled_env() -> dict[str, str]:
    return {
        "PROTO_PAPER_AUTONOMY_ENABLED": "true",
        "PROTO_PAPER_AUTONOMY_SYMBOL": "BTC",
        "PROTO_PAPER_AUTONOMY_IMBALANCE_TRIGGER": "0.65",
        "PROTO_PAPER_AUTONOMY_COOLDOWN_SECONDS": "20",
        "PROTO_PAPER_AUTONOMY_QUANTITY": "0.001",
        "PROTO_PAPER_AUTONOMY_MAX_SPREAD_BPS": "20",
        "PROTO_PAPER_AUTONOMY_STOP_LOSS_FRACTION": "0.02",
    }


def setup_function() -> None:
    reset_runtime_state()
    module.bootstrap_state.enabled = False
    module.bootstrap_state.configured = False
    module.bootstrap_state.started = False
    module.bootstrap_state.config = None
    module.bootstrap_state.watchdog_running = False
    module.bootstrap_state.watchdog_checks = 0
    module.bootstrap_state.watchdog_restarts = 0
    module.bootstrap_state.watchdog_failures = 0
    module.bootstrap_state.watchdog_last_check_at = None
    module.bootstrap_state.watchdog_last_error = None


def teardown_function() -> None:
    reset_runtime_state()


def test_bootstrap_is_disabled_by_default() -> None:
    assert load_bootstrap_config({}) is None


def test_enabled_bootstrap_requires_every_execution_parameter() -> None:
    env = _enabled_env()
    env.pop("PROTO_PAPER_AUTONOMY_STOP_LOSS_FRACTION")

    with pytest.raises(ValueError, match="STOP_LOSS_FRACTION"):
        load_bootstrap_config(env)


def test_enabled_bootstrap_builds_explicit_paper_config() -> None:
    config = load_bootstrap_config(_enabled_env())

    assert config is not None
    assert config.symbol == "BTC"
    assert config.quantity == pytest.approx(0.001)
    assert config.imbalance_trigger == pytest.approx(0.65)
    assert config.cooldown_seconds == pytest.approx(20.0)
    assert config.max_spread_bps == pytest.approx(20.0)
    assert config.stop_loss_fraction == pytest.approx(0.02)


def test_invalid_stop_loss_is_rejected_before_runtime_start() -> None:
    env = _enabled_env()
    env["PROTO_PAPER_AUTONOMY_STOP_LOSS_FRACTION"] = "0"

    with pytest.raises(ValueError):
        load_bootstrap_config(env)


class AutopilotProbe:
    def __init__(self) -> None:
        self.running = False
        self.starts = 0

    async def start(self, config) -> dict[str, object]:
        self.starts += 1
        self.running = True
        return {"config": config.model_dump(mode="json")}


@pytest.mark.asyncio
async def test_watchdog_restarts_dead_bootstrap_owned_worker(monkeypatch) -> None:
    config = load_bootstrap_config(_enabled_env())
    assert config is not None
    probe = AutopilotProbe()
    monkeypatch.setattr(module, "paper_autopilot", probe)
    monkeypatch.setattr(module, "simulation_execution_allowed", lambda: True)
    module.bootstrap_state.started = True
    module.bootstrap_state.configured = True
    module.bootstrap_state.config = config
    runtime.mode = SystemMode.PAPER_TRADING
    runtime.running = True
    runtime.kill_switch = KillSwitchState.ARMED

    restarted = await module.reconcile_configured_paper_autonomy()

    assert restarted is True
    assert probe.starts == 1
    assert module.bootstrap_state.watchdog_restarts == 1
    assert module.bootstrap_state.watchdog_failures == 0
    assert module.bootstrap_state.last_reason == "WATCHDOG_AUTOPILOT_RESTARTED"


@pytest.mark.asyncio
async def test_watchdog_never_overrides_kill_switch(monkeypatch) -> None:
    config = load_bootstrap_config(_enabled_env())
    assert config is not None
    probe = AutopilotProbe()
    monkeypatch.setattr(module, "paper_autopilot", probe)
    monkeypatch.setattr(module, "simulation_execution_allowed", lambda: True)
    module.bootstrap_state.started = True
    module.bootstrap_state.configured = True
    module.bootstrap_state.config = config
    runtime.mode = SystemMode.PAPER_TRADING
    runtime.running = True
    runtime.kill_switch = KillSwitchState.TRIGGERED

    restarted = await module.reconcile_configured_paper_autonomy()

    assert restarted is False
    assert probe.starts == 0
    assert module.bootstrap_state.watchdog_restarts == 0
    assert module.bootstrap_state.last_reason == "WATCHDOG_SAFETY_GATE_BLOCKED"


@pytest.mark.asyncio
async def test_watchdog_never_reclaims_operator_changed_mode(monkeypatch) -> None:
    config = load_bootstrap_config(_enabled_env())
    assert config is not None
    probe = AutopilotProbe()
    monkeypatch.setattr(module, "paper_autopilot", probe)
    monkeypatch.setattr(module, "simulation_execution_allowed", lambda: True)
    module.bootstrap_state.started = True
    module.bootstrap_state.configured = True
    module.bootstrap_state.config = config
    runtime.mode = SystemMode.SHADOW
    runtime.running = True
    runtime.kill_switch = KillSwitchState.ARMED

    restarted = await module.reconcile_configured_paper_autonomy()

    assert restarted is False
    assert probe.starts == 0
    assert module.bootstrap_state.last_reason == "WATCHDOG_RUNTIME_NOT_OWNED"
