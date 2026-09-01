from fastapi.testclient import TestClient

from apps.api.app.app_state import runtime
from apps.api.app.main import app
from apps.api.app.models import KillSwitchState, SystemMode

client = TestClient(app)


def _risk_for(mode: SystemMode, *, running: bool = True) -> dict[str, object]:
    previous = (runtime.mode, runtime.running, runtime.kill_switch)
    try:
        runtime.mode = mode
        runtime.running = running
        runtime.kill_switch = KillSwitchState.ARMED
        response = client.get("/risk")
        assert response.status_code == 200
        return response.json()
    finally:
        runtime.mode, runtime.running, runtime.kill_switch = previous


def test_live_monitoring_reports_simulation_blocked() -> None:
    body = _risk_for(SystemMode.LIVE_MONITORING)

    assert body["mode"] == "LIVE_MONITORING"
    assert body["simulation_allowed"] is False
    assert body["financial_connectivity"] is False
    assert body["real_money_execution"] is False


def test_historical_replay_reports_simulation_blocked() -> None:
    body = _risk_for(SystemMode.HISTORICAL_REPLAY)

    assert body["simulation_allowed"] is False


def test_paper_trading_reports_simulation_allowed_only_while_running() -> None:
    running = _risk_for(SystemMode.PAPER_TRADING, running=True)
    stopped = _risk_for(SystemMode.PAPER_TRADING, running=False)

    assert running["simulation_allowed"] is True
    assert stopped["simulation_allowed"] is False


def test_simulation_reports_simulation_allowed_when_running_and_armed() -> None:
    body = _risk_for(SystemMode.SIMULATION)

    assert body["simulation_allowed"] is True
