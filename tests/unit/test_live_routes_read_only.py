from fastapi.testclient import TestClient

from apps.api.app.main import app

client = TestClient(app)


def test_live_monitoring_http_surface_has_no_mutating_controls() -> None:
    assert client.post("/live/start").status_code == 404
    assert client.post("/live/stop").status_code == 404


def test_live_monitoring_http_surface_exposes_expected_read_only_routes() -> None:
    assert client.get("/live/status").status_code == 200
    assert client.get("/live/source-health").status_code == 200
    assert client.get("/live/ready").status_code == 503
    assert client.get("/live/market-data").status_code == 200
    assert client.get("/live/metrics/prometheus").status_code == 200


def test_live_monitoring_responses_are_never_cacheable() -> None:
    paths = (
        "/live/status",
        "/live/source-health",
        "/live/ready",
        "/live/market-data",
        "/live/metrics/prometheus",
        "/live/market-data/DOGE",
        "/live/analytics/DOGE",
    )

    for path in paths:
        response = client.get(path)
        assert response.headers["cache-control"] == "no-store, max-age=0"


def test_live_readiness_failure_exposes_retry_after_without_mutating_controls() -> None:
    response = client.get("/live/ready")

    assert response.status_code == 503
    assert response.headers["retry-after"] == "1"
    assert response.headers["cache-control"] == "no-store, max-age=0"
    assert response.json()["status"] == "not_ready"


def test_live_prometheus_metrics_keep_financial_capabilities_disabled() -> None:
    response = client.get("/live/metrics/prometheus")
    body = response.text

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/plain")
    assert "proto_live_financial_connectivity 0" in body
    assert "proto_live_real_money_execution 0" in body
    assert "proto_live_connected 0" in body
    assert "proto_live_symbol_fresh{symbol=\"BTC\"} 0" in body
    assert "proto_live_symbol_fresh{symbol=\"ETH\"} 0" in body
    assert "proto_live_symbol_fresh{symbol=\"SOL\"} 0" in body
