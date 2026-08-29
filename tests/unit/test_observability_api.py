import json

from fastapi.testclient import TestClient

from apps.api.app.main import app
from apps.api.app.observability import access_log

client = TestClient(app)


def test_request_id_is_generated_and_echoed() -> None:
    response = client.get("/health")

    assert response.status_code == 200
    assert response.headers["X-Request-ID"]


def test_provided_request_id_is_preserved() -> None:
    response = client.get("/health", headers={"X-Request-ID": "research-test-id"})

    assert response.status_code == 200
    assert response.headers["X-Request-ID"] == "research-test-id"


def test_readiness_is_ready_without_external_persistence() -> None:
    response = client.get("/ready")
    body = response.json()

    assert response.status_code == 200
    assert body["status"] == "ready"
    assert body["database"] == "disabled"
    assert body["mode"] == "SIMULATION"


def test_metrics_count_requests_and_preserve_simulation_boundary() -> None:
    client.get("/health")
    response = client.get("/metrics")
    body = response.json()

    assert response.status_code == 200
    assert body["mode"] == "SIMULATION"
    assert body["real_money_execution"] is False
    assert body["request_count"] >= 1
    assert body["by_path"]["/health"] >= 1
    assert body["average_latency_ms"] >= 0


def test_calibration_endpoint_exposes_quality_metrics() -> None:
    response = client.post(
        "/calibration/score",
        json={
            "observations": [
                {"predicted_probability": 0.8, "outcome": 1},
                {"predicted_probability": 0.3, "outcome": 0},
            ]
        },
    )
    body = response.json()

    assert response.status_code == 200
    assert body["count"] == 2
    assert body["brier_score"] == 0.065
    assert body["observed_frequency"] == 0.5
    assert body["calibration_bias"] == 0.05


def test_access_log_is_structured_json() -> None:
    payload = json.loads(
        access_log(
            request_id="abc-123",
            method="GET",
            path="/health",
            status_code=200,
            latency_ms=1.23456789,
        )
    )

    assert payload == {
        "event": "http_request",
        "latency_ms": 1.234568,
        "method": "GET",
        "path": "/health",
        "request_id": "abc-123",
        "status_code": 200,
    }
