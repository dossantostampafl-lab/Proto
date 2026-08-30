from fastapi.routing import APIWebSocketRoute

from apps.api.app.live_app import app


_ALLOWED_HTTP_METHODS = {"GET", "HEAD", "OPTIONS"}
_FORBIDDEN_PATH_SEGMENTS = {
    "accounts",
    "deposit",
    "fills",
    "portfolio",
    "probability",
    "replay",
    "simulation",
    "withdraw",
}
_ALLOWED_WEBSOCKETS = {"/ws/market-data", "/ws/orderbook"}


def test_standalone_live_openapi_has_no_mutating_http_operations() -> None:
    schema = app.openapi()

    for path, operations in schema["paths"].items():
        for method in operations:
            if method.lower() == "parameters":
                continue
            assert method.upper() in _ALLOWED_HTTP_METHODS, f"unexpected {method} {path}"


def test_standalone_live_surface_contains_no_legacy_financial_paths() -> None:
    schema = app.openapi()

    for path in schema["paths"]:
        segments = {segment.lower() for segment in path.split("/") if segment}
        assert segments.isdisjoint(_FORBIDDEN_PATH_SEGMENTS), path


def test_standalone_live_websockets_are_allowlisted_observation_channels_only() -> None:
    websocket_paths = {
        route.path for route in app.routes if isinstance(route, APIWebSocketRoute)
    }

    assert websocket_paths == _ALLOWED_WEBSOCKETS
