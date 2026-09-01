from __future__ import annotations

from apps.api.app.main import app


def test_frozen_holdout_routes_are_registered() -> None:
    paths = {
        path
        for route in app.routes
        if (path := getattr(route, "path", None)) is not None
    }

    assert "/research/validation/holdout/seal" in paths
    assert "/research/validation/holdout/evaluate" in paths
