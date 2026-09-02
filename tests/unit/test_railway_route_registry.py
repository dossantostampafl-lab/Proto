from collections import Counter

from fastapi.routing import APIRoute

from apps.api.app.railway_app import app


def _http_route_counts() -> Counter[tuple[str, str]]:
    counts: Counter[tuple[str, str]] = Counter()
    for route in app.routes:
        if not isinstance(route, APIRoute):
            continue
        for method in route.methods or set():
            counts[(method, route.path)] += 1
    return counts


def test_railway_registers_live_http_routes_once() -> None:
    counts = _http_route_counts()

    for path in (
        "/live/status",
        "/live/source-health",
        "/live/ready",
        "/live/market-data",
        "/live/market-data/{symbol}",
        "/live/history/{symbol}",
        "/live/analytics/{symbol}",
        "/live/metrics/prometheus",
    ):
        assert counts[("GET", path)] == 1, path
