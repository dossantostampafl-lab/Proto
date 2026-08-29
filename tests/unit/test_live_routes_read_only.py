from fastapi.testclient import TestClient

from apps.api.app.main import app

client = TestClient(app)


def test_live_monitoring_http_surface_rejects_mutating_controls() -> None:
    assert client.post("/live/start").status_code == 405
    assert client.post("/live/stop").status_code == 405


def test_live_monitoring_http_surface_exposes_expected_read_only_routes() -> None:
    assert client.get("/live/status").status_code == 200
    assert client.get("/live/source-health").status_code == 200
    assert client.get("/live/ready").status_code == 503
    assert client.get("/live/market-data").status_code == 200
