import asyncio
from time import monotonic

import pytest

from apps.api.app import paper_autopilot as module
from apps.api.app.app_state import runtime
from apps.api.app.models import KillSwitchState, SystemMode
from apps.api.app.paper_autopilot import PaperAutopilotConfig, PaperAutopilotService


@pytest.fixture(autouse=True)
def restore_runtime():
    previous = (runtime.mode, runtime.running, runtime.kill_switch)
    runtime.mode = SystemMode.PAPER_TRADING
    runtime.running = True
    runtime.kill_switch = KillSwitchState.ARMED
    yield
    runtime.mode, runtime.running, runtime.kill_switch = previous


def fresh_status(symbol: str) -> dict[str, object]:
    return {
        "running": True,
        "receiving_data": True,
        "source_message_fresh": True,
        "feed_health": {"connected": True},
        "symbol_health": {
            symbol: {"fresh": True, "receipt_fresh": True, "current_connection": True}
        },
        "financial_connectivity": False,
        "real_money_execution": False,
    }


@pytest.mark.asyncio
async def test_running_symbol_update_resets_signal_consumption_but_keeps_cooldown(monkeypatch) -> None:
    service = PaperAutopilotService()
    service._config = PaperAutopilotConfig(symbol="BTC")  # noqa: SLF001
    service._armed_side = "BUY"  # noqa: SLF001
    service._last_signal = {"symbol": "BTC", "imbalance": 0.8}  # noqa: SLF001
    cooldown_clock = monotonic()
    service._last_action_monotonic = cooldown_clock  # noqa: SLF001
    service._task = asyncio.create_task(asyncio.sleep(60))  # noqa: SLF001
    monkeypatch.setattr(module.live_monitor, "status", lambda: fresh_status("ETH"))

    try:
        state = await service.start(PaperAutopilotConfig(symbol="ETH"))
        assert state["config"]["symbol"] == "ETH"
        assert state["last_reason"] == "CONFIG_UPDATED"
        assert service._armed_side is None  # noqa: SLF001
        assert service._last_signal is None  # noqa: SLF001
        assert service._last_action_monotonic == cooldown_clock  # noqa: SLF001
    finally:
        await service.stop()


@pytest.mark.asyncio
async def test_same_symbol_config_update_preserves_consumed_signal(monkeypatch) -> None:
    service = PaperAutopilotService()
    service._config = PaperAutopilotConfig(symbol="BTC", imbalance_trigger=0.65)  # noqa: SLF001
    service._armed_side = "SELL"  # noqa: SLF001
    service._last_signal = {"symbol": "BTC", "imbalance": -0.8}  # noqa: SLF001
    service._task = asyncio.create_task(asyncio.sleep(60))  # noqa: SLF001
    monkeypatch.setattr(module.live_monitor, "status", lambda: fresh_status("BTC"))

    try:
        state = await service.start(
            PaperAutopilotConfig(symbol="BTC", imbalance_trigger=0.7)
        )
        assert state["config"]["imbalance_trigger"] == 0.7
        assert service._armed_side == "SELL"  # noqa: SLF001
        assert service._last_signal == {"symbol": "BTC", "imbalance": -0.8}  # noqa: SLF001
    finally:
        await service.stop()
