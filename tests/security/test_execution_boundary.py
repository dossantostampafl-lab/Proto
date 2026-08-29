from fastapi.routing import APIRoute

from apps.api.app.main import app

FORBIDDEN_ROUTE_TERMS = {
    "broker",
    "deposit",
    "exchange",
    "leverage",
    "live-order",
    "live_order",
    "withdraw",
}


def test_api_has_no_real_money_execution_routes() -> None:
    routes = {
        route.path.lower()
        for route in app.routes
        if isinstance(route, APIRoute)
    }

    for path in routes:
        assert not any(term in path for term in FORBIDDEN_ROUTE_TERMS), path


def test_execution_endpoint_is_explicitly_simulated() -> None:
    post_routes = {
        route.path
        for route in app.routes
        if isinstance(route, APIRoute) and "POST" in route.methods
    }

    assert "/v1/simulate" in post_routes
    assert "/v1/order" not in post_routes
    assert "/orders" not in post_routes
    assert "/order" not in post_routes
