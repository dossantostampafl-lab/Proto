from fastapi.testclient import TestClient

from apps.api.app.main import app
from apps.api.app.research import _prometheus_metrics


client = TestClient(app)


def test_prometheus_export_contains_core_simulation_metrics() -> None:
    payload = _prometheus_metrics(
        {
            "counters": {"simulation_requests": 3},
            "average_simulation_latency_ms": 1.25,
            "latency_samples": 2,
            "http_request_count": 4,
            "http_error_count": 1,
            "average_http_latency_ms": 2.5,
            "http_by_path": {"/health": 4},
            "http_by_status": {"200": 3, "500": 1},
        }
    )

    assert "proto_simulation_latency_ms 1.25" in payload
    assert 'proto_runtime_events_total{event="simulation_requests"} 3' in payload
    assert 'proto_http_path_requests_total{path="/health"} 4' in payload
    assert 'proto_http_status_total{status="500"} 1' in payload


def test_observability_snapshot_preserves_simulation_boundary() -> None:
    response = client.get("/research/observability/snapshot")
    body = response.json()

    assert response.status_code == 200
    assert body["scope"] == "SIMULATION_REPLAY_ONLY"
    assert body["real_money_execution"] is False
    assert "http_request_count" in body
    assert "average_http_latency_ms" in body


def test_prometheus_endpoint_uses_text_exposition_format() -> None:
    response = client.get("/research/observability/prometheus")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/plain")
    assert "# TYPE proto_http_requests_total counter" in response.text
    assert "proto_http_requests_total" in response.text
