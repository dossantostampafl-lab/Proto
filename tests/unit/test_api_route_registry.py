from collections import Counter
from collections.abc import Iterator

from apps.api.app.main import app


def _endpoints(routes, prefix: str = "") -> Iterator[tuple[str, str]]:
    """Yield (method, full path) for every endpoint, descending into included routers."""
    for route in routes:
        included = getattr(route, "original_router", None)
        if included is not None:  # FastAPI keeps include_router() targets nested
            yield from _endpoints(included.routes, prefix + route.include_context.prefix)
            continue
        path = getattr(route, "path", None)
        if path is None or type(route).__name__ == "Mount":  # static dashboard
            continue
        for method in getattr(route, "methods", None) or {"WEBSOCKET"}:
            yield method, prefix + path


def test_every_route_is_registered_exactly_once() -> None:
    counts = Counter(_endpoints(app.router.routes))

    duplicates = sorted(key for key, count in counts.items() if count > 1)
    assert duplicates == [], f"routes registered more than once: {duplicates}"
    assert ("GET", "/live/status") in counts
    assert ("POST", "/v1/simulate") in counts
    assert ("WEBSOCKET", "/ws/market-data") in counts
