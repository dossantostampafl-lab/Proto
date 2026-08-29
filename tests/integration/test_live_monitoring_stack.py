from fastapi.testclient import TestClient

from apps.api.app.main import app


def test_live_monitoring_stack_starts_without_financial_connectivity() -> None:
    with TestClient(app) as client:
        event_ready = client.get("/events/ready")
        live_status = client.get("/live/status")
        live_ready = client.get("/live/ready")
        circuits = client.get("/safety/circuit-breakers")

    assert event_ready.status_code == 200
    assert event_ready.json()["ready"] is True
    assert event_ready.json()["backend"] == "memory"

    assert live_status.status_code == 200
    assert live_status.json()["mode"] == "LIVE_MONITORING"
    assert live_status.json()["financial_connectivity"] is False
    assert live_status.json()["real_money_execution"] is False

    assert live_ready.status_code == 503
    assert live_ready.json()["status"] == "not_ready"

    assert circuits.status_code == 200
    assert circuits.json()["financial_connectivity"] is False
    assert circuits.json()["real_money_execution"] is False
    assert "STALE_DATA" in circuits.json()["reasons"]
