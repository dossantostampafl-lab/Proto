from datetime import UTC, datetime

import pytest
from fastapi import HTTPException

from apps.api.app import paper_autopilot as module
from apps.api.app.app_state import runtime
from apps.api.app.models import KillSwitchState, SimulationResult, SystemMode
from apps.api.app.paper_autopilot import PaperAutopilotConfig, PaperAutopilotService


@pytest.fixture(autouse=True)
def restore_runtime():
    previous = (runtime.mode, runtime.running, runtime.kill_switch)
    yield
    runtime.mode, runtime.running, runtime.kill_switch = previous


def _paper_runtime() -> None:
    runtime.mode = SystemMode.PAPER_TRADING
    runtime.running = True
    runtime.kill_switch = KillSwitchState.ARMED


def _fresh_live(monkeypatch, symbol: str = "BTC") -> None:
    monkeypatch.setattr(
        module.live_monitor,
        "status",
        lambda: {
            "running": True,
            "receiving_data": True,
            "fresh_symbols": [symbol],
            "financial_connectivity": False,
            "real_money_execution": False,
        },
    )


@pytest.mark.asyncio
async def test_autopilot_start_fails_closed_outside_paper_runtime() -> None:
    runtime.mode = SystemMode.LIVE_MONITORING
    runtime.running = True
    runtime.kill_switch = KillSwitchState.ARMED
    service = PaperAutopilotService()

    with pytest.raises(HTTPException) as error:
        await service.start(PaperAutopilotConfig())

    assert error.value.status_code == 409
    assert service.running is False
    assert service.status()["financial_connectivity"] is False
    assert service.status()["real_money_execution"] is False


@pytest.mark.asyncio
async def test_autopilot_stale_live_data_blocks_submission(monkeypatch) -> None:
    _paper_runtime()
    service = PaperAutopilotService()
    service._config = PaperAutopilotConfig(  # noqa: SLF001 - white-box safety regression
        symbol="BTC",
        imbalance_trigger=0.6,
        cooldown_seconds=5,
        quantity=0.001,
        max_spread_bps=20,
    )
    monkeypatch.setattr(
        module.live_monitor,
        "status",
        lambda: {
            "running": True,
            "receiving_data": True,
            "fresh_symbols": [],
            "financial_connectivity": False,
            "real_money_execution": False,
        },
    )
    monkeypatch.setattr(
        module.live_monitor,
        "snapshot",
        lambda symbol: (_ for _ in ()).throw(AssertionError("stale snapshot must not be read")),
    )

    async def fail_if_called(request):
        raise AssertionError("simulator must not be called with stale live data")

    monkeypatch.setattr(module, "simulate", fail_if_called)

    await service._cycle()  # noqa: SLF001

    state = service.status()
    assert state["last_reason"] == "WAITING_FOR_FRESH_LIVE_DATA"
    assert state["live_market_ready"] is False
    assert state["counters"]["submissions"] == 0


@pytest.mark.asyncio
async def test_autopilot_same_signal_regime_submits_only_once(monkeypatch) -> None:
    _paper_runtime()
    _fresh_live(monkeypatch)
    service = PaperAutopilotService()
    service._config = PaperAutopilotConfig(  # noqa: SLF001 - white-box safety regression
        symbol="BTC",
        imbalance_trigger=0.6,
        cooldown_seconds=5,
        quantity=0.001,
        max_spread_bps=20,
    )

    now = datetime.now(UTC).isoformat()
    frame = {
        "symbol": "BTC",
        "bid": 100.0,
        "ask": 100.01,
        "bid_size": 2.0,
        "ask_size": 2.0,
        "timestamp": now,
        "received_at": now,
    }
    analytics = {"current_imbalance": 0.8, "realized_volatility": 0.02}
    monkeypatch.setattr(module.live_monitor, "snapshot", lambda symbol: frame)
    monkeypatch.setattr(module.live_monitor, "analytics", lambda symbol: analytics)

    calls = 0

    async def fake_simulate(request):
        nonlocal calls
        calls += 1
        assert request.order.asset.value == "BTC"
        assert request.order.side.value == "BUY"
        return SimulationResult(accepted=False, reason="test risk rejection")

    monkeypatch.setattr(module, "simulate", fake_simulate)

    await service._cycle()  # noqa: SLF001 - exercise deterministic decision cycle
    await service._cycle()  # noqa: SLF001 - persistent signal must not accumulate

    state = service.status()
    assert calls == 1
    assert state["live_market_ready"] is True
    assert state["counters"]["submissions"] == 1
    assert state["counters"]["rejected"] == 1
    assert state["last_reason"] == "SIGNAL_ALREADY_CONSUMED"


@pytest.mark.asyncio
async def test_autopilot_spread_and_liquidity_guards_block_submission(monkeypatch) -> None:
    _paper_runtime()
    _fresh_live(monkeypatch)
    service = PaperAutopilotService()
    service._config = PaperAutopilotConfig(  # noqa: SLF001
        imbalance_trigger=0.6,
        cooldown_seconds=5,
        quantity=1.0,
        max_spread_bps=5,
    )
    now = datetime.now(UTC).isoformat()
    frame = {
        "symbol": "BTC",
        "bid": 100.0,
        "ask": 100.2,
        "bid_size": 0.5,
        "ask_size": 0.5,
        "timestamp": now,
        "received_at": now,
    }
    monkeypatch.setattr(module.live_monitor, "snapshot", lambda symbol: frame)
    monkeypatch.setattr(
        module.live_monitor,
        "analytics",
        lambda symbol: {"current_imbalance": 0.9, "realized_volatility": 0.02},
    )

    async def fail_if_called(request):
        raise AssertionError("simulator must not be called while a guard is active")

    monkeypatch.setattr(module, "simulate", fail_if_called)
    await service._cycle()  # noqa: SLF001
    assert service.status()["last_reason"] == "SPREAD_GUARD"

    service._config = service._config.model_copy(update={"max_spread_bps": 30})  # noqa: SLF001
    await service._cycle()  # noqa: SLF001
    assert service.status()["last_reason"] == "LIQUIDITY_GUARD"
    assert service.status()["counters"]["submissions"] == 0


@pytest.mark.asyncio
async def test_autopilot_worker_can_start_and_stop_cleanly(monkeypatch) -> None:
    _paper_runtime()
    _fresh_live(monkeypatch)
    service = PaperAutopilotService()

    started = await service.start(PaperAutopilotConfig())
    assert started["running"] is True
    assert started["paper_runtime_ready"] is True
    assert started["live_market_ready"] is True

    stopped = await service.stop()
    assert stopped["running"] is False
    assert stopped["last_reason"] == "STOPPED"
