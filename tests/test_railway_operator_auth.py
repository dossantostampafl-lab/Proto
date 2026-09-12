from fastapi.testclient import TestClient

from apps.api.app.railway_app import app
from apps.api.app.settings import settings


def test_public_get_health_remains_available(monkeypatch) -> None:
    monkeypatch.setattr(settings, "operator_api_token", "o" * 32, raising=False)
    with TestClient(app) as client:
        response = client.get("/health")
    assert response.status_code == 200


def test_mutating_operator_route_rejects_missing_token(monkeypatch) -> None:
    monkeypatch.setattr(settings, "operator_api_token", "o" * 32, raising=False)
    with TestClient(app) as client:
        response = client.post("/paper/start")
    assert response.status_code == 401


def test_mutating_operator_route_rejects_wrong_token(monkeypatch) -> None:
    monkeypatch.setattr(settings, "operator_api_token", "o" * 32, raising=False)
    with TestClient(app) as client:
        response = client.post(
            "/paper/start",
            headers={"X-Proto-Operator-Token": "wrong"},
        )
    assert response.status_code == 401


def test_mutating_operator_route_accepts_valid_token(monkeypatch) -> None:
    token = "o" * 32
    monkeypatch.setattr(settings, "operator_api_token", token, raising=False)
    with TestClient(app) as client:
        response = client.post(
            "/paper/start",
            headers={"X-Proto-Operator-Token": token},
        )
    assert response.status_code == 200


def test_creation_bridge_keeps_independent_authentication(monkeypatch) -> None:
    monkeypatch.setattr(settings, "operator_api_token", "o" * 32, raising=False)
    with TestClient(app) as client:
        response = client.post("/creation/missions", json={})
    operator_rejection = (
        response.status_code == 401
        and response.json().get("detail") == "Operator identity was not verified"
    )
    assert not operator_rejection
