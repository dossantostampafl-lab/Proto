from fastapi.testclient import TestClient

from apps.api.app.main import app


def test_live_history_is_read_only_and_fails_closed_when_persistence_disabled() -> None:
    with TestClient(app) as client:
        response = client.get("/live/history/BTC?limit=10")

    assert response.status_code == 503
    assert response.json()["detail"] == "live persistence is disabled"
    assert response.headers["cache-control"] == "no-store, max-age=0"


def test_live_history_rejects_symbols_outside_live_allowlist() -> None:
    with TestClient(app) as client:
        response = client.get("/live/history/DOGE")

    assert response.status_code == 404
    assert "allowlist" in response.json()["detail"]
    assert response.headers["cache-control"] == "no-store, max-age=0"


def test_live_history_has_no_mutating_http_operations() -> None:
    with TestClient(app) as client:
        response = client.post("/live/history/BTC")

    assert response.status_code == 405
