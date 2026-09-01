from fastapi.testclient import TestClient

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
    assert status["financial_connectivity"] is False
    assert status["real_money_execution"] is False


def test_paper_stop_disables_simulated_execution() -> None:
    client.post("/paper/start")
    response = client.post("/paper/stop")
    assert response.status_code == 200
    assert response.json()["running"] is False
    assert client.get("/paper/status").json()["paper_execution_enabled"] is False
