import pytest
from fastapi.testclient import TestClient

from apps.api.app import paper_control as module
from apps.api.app.app_state import reset_runtime_state, runtime
from apps.api.app.models import SystemMode
from apps.api.app.railway_app import app

client = TestClient(app)


def setup_function() -> None:
    reset_runtime_state()


def teardown_function() -> None:
    reset_runtime_state()


def test_paper_start_enables_only_internal_paper_runtime() -> None:
    response = client.post("/paper/start")
    assert response.status_code == 200
    body = response.json()
    assert body["mode"] == "PAPER_TRADING"
    assert body["running"] is True
    assert runtime.mode == SystemMode.PAPER_TRADING

    status = client.get("/paper/status").json()
    assert status["paper_execution_enabled"] is True
    assert status["autopilot_running"] is False
    assert status["financial_connectivity"] is False
    assert status["real_money_execution"] is False


def test_paper_stop_disables_simulated_execution() -> None:
    client.post("/paper/start")
    response = client.post("/paper/stop")
    assert response.status_code == 200
    assert response.json()["running"] is False
    status = client.get("/paper/status").json()
    assert status["paper_execution_enabled"] is False
    assert status["autopilot_running"] is False


class _AutopilotProbe:
    running = True

    def __init__(self) -> None:
        self.stop_calls = 0

    async def stop(self) -> dict[str, object]:
        self.stop_calls += 1
        self.running = False
        return {"running": False}


@pytest.mark.asyncio
async def test_paper_stop_also_disarms_persistent_autopilot(monkeypatch) -> None:
    client.post("/paper/start")
    probe = _AutopilotProbe()
    monkeypatch.setattr(module, "paper_autopilot", probe)

    result = await module.stop_paper_trading()

    assert result.running is False
    assert probe.stop_calls == 1
    assert probe.running is False
    status = module.paper_status()
    assert status["paper_execution_enabled"] is False
    assert status["autopilot_running"] is False
