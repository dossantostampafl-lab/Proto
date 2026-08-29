from fastapi.testclient import TestClient

from apps.api.app.live_app import app


def test_standalone_live_app_exposes_only_safe_runtime_surfaces() -> None:
    paths = {
        path
        for route in app.routes
        if isinstance((path := getattr(route, "path", None)), str)
    }

    assert "/health" in paths
    assert "/events/ready" in paths
    assert "/live/ready" in paths
    assert "/live/market-data" in paths
    assert "/live/history/{symbol}" in paths
    assert "/ws/market-data" in paths
    assert "/ws/orderbook" in paths

    forbidden = {
        "/probability/estimate",
        "/edge/evaluate",
        "/v1/simulate",
        "/v1/portfolio",
        "/v1/fills",
        "/simulation/start",
        "/replay/start",
        "/killswitch/trigger",
        "/ws/signals",
        "/ws/risk",
        "/ws/portfolio",
        "/ws/fills",
    }
    assert paths.isdisjoint(forbidden)


def test_standalone_live_app_is_http_read_only_and_security_hardened() -> None:
    with TestClient(app) as client:
        health = client.get("/health")
        legacy_write = client.post("/v1/simulate", json={})
        legacy_read = client.get("/v1/portfolio")

    assert health.status_code == 200
    assert health.json()["financial_connectivity"] is False
    assert health.json()["real_money_execution"] is False
    assert health.headers["cache-control"] == "no-store"
    assert health.headers["x-content-type-options"] == "nosniff"
    assert health.headers["x-frame-options"] == "DENY"
    assert health.headers["referrer-policy"] == "no-referrer"
    assert "camera=()" in health.headers["permissions-policy"]
    assert health.headers["content-security-policy"] == "default-src 'none'; frame-ancestors 'none'"
    assert "x-request-id" in health.headers

    assert legacy_write.status_code == 405
    assert legacy_write.json()["detail"] == "standalone live monitoring API is read-only"
    assert legacy_read.status_code == 404
