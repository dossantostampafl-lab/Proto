from fastapi.routing import APIRoute, APIWebSocketRoute
from fastapi.testclient import TestClient

from apps.api.app.live_app import app


_FORBIDDEN_HTTP_PATHS = {
    "/probability/estimate",
    "/edge/evaluate",
    "/risk",
    "/v1/simulate",
    "/v1/portfolio",
    "/v1/fills",
    "/simulation/start",
    "/simulation/stop",
    "/replay/start",
    "/replay/step",
    "/killswitch/trigger",
    "/killswitch/reset",
}


def test_standalone_live_app_exposes_no_legacy_financial_or_simulation_routes() -> None:
    http_paths = {
        route.path
        for route in app.routes
        if isinstance(route, APIRoute)
    }
    websocket_paths = {
        route.path
        for route in app.routes
        if isinstance(route, APIWebSocketRoute)
    }

    assert http_paths.isdisjoint(_FORBIDDEN_HTTP_PATHS)
    assert websocket_paths == {"/ws/market-data", "/ws/orderbook"}


def test_standalone_live_http_surface_is_read_only() -> None:
    violations: list[tuple[str, set[str]]] = []
    for route in app.routes:
        if not isinstance(route, APIRoute):
            continue
        methods = set(route.methods or ())
        if methods - {"GET", "HEAD"}:
            violations.append((route.path, methods))

    assert violations == []


def test_standalone_health_declares_read_only_invariants_and_security_headers() -> None:
    with TestClient(app) as client:
        response = client.get("/health")

    assert response.status_code == 200
    assert response.json()["mode"] == "LIVE_MONITORING"
    assert response.json()["source"] == "PUBLIC_READ_ONLY"
    assert response.json()["financial_connectivity"] is False
    assert response.json()["real_money_execution"] is False
    assert response.headers["X-Content-Type-Options"] == "nosniff"
    assert response.headers["X-Frame-Options"] == "DENY"
    assert response.headers["Cache-Control"] == "no-store"
    assert response.headers["X-Request-ID"]


def test_standalone_live_surface_rejects_mutating_method() -> None:
    with TestClient(app) as client:
        response = client.post("/live/status")

    assert response.status_code == 405
    assert response.headers["Cache-Control"] == "no-store"
