from fastapi.testclient import TestClient

from apps.api.app.main import app
from apps.api.app.observability import RuntimeMetrics


def test_prometheus_snapshot_has_stable_names_and_numeric_values() -> None:
    runtime_metrics = RuntimeMetrics()
    runtime_metrics.increment("live_data_ticks", 3)
    runtime_metrics.record_http(path="/health", status_code=200, latency_ms=2.5)

    output = runtime_metrics.prometheus()

    assert "proto_http_requests_total 1" in output
    assert "proto_live_data_ticks_total 3" in output
    assert "proto_http_latency_milliseconds_average 2.5" in output


def test_prometheus_endpoint_uses_text_exposition_format() -> None:
    with TestClient(app) as client:
        response = client.get("/metrics/prometheus")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/plain")
    assert "proto_http_requests_total" in response.text
