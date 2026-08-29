from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

import pytest
from fastapi.testclient import TestClient

from apps.api.app.main import app
from apps.api.app.models import SystemMode

EXPECTED_STATUS_FIELDS = {
    "mode",
    "state",
    "source",
    "symbol",
    "last_tick_at",
    "last_sequence",
    "received",
    "rejected",
    "reconnect_attempts",
    "last_error",
    "stale",
    "latency_ms",
    "staleness_ms",
    "read_only",
}
LIVE_STATES = {"STOPPED", "CONNECTING", "STREAMING", "BACKOFF"}


def _live_contract_available() -> bool:
    return "LIVE_DATA_READ_ONLY" in SystemMode.__members__


@contextmanager
def _client() -> Iterator[TestClient]:
    with TestClient(app) as client:
        # Avoid coupling tests to state left by another module.
        client.post("/live/stop")
        yield client
        client.post("/live/stop")


pytestmark = pytest.mark.skipif(
    not _live_contract_available(),
    reason="LIVE_DATA_READ_ONLY backend is integrated on a separate branch",
)


def _assert_status_contract(payload: dict[str, Any]) -> None:
    assert EXPECTED_STATUS_FIELDS <= payload.keys()
    assert payload["mode"] == "LIVE_DATA_READ_ONLY"
    assert payload["state"] in LIVE_STATES
    assert payload["read_only"] is True
    assert isinstance(payload["received"], int) and payload["received"] >= 0
    assert isinstance(payload["rejected"], int) and payload["rejected"] >= 0
    assert isinstance(payload["reconnect_attempts"], int)
    assert payload["reconnect_attempts"] >= 0
    assert payload["latency_ms"] is None or payload["latency_ms"] >= 0
    assert payload["staleness_ms"] is None or payload["staleness_ms"] >= 0


def test_live_status_exposes_operational_contract_and_read_only_proof() -> None:
    with _client() as client:
        response = client.get("/live/status")

    assert response.status_code == 200
    payload = response.json()
    _assert_status_contract(payload)
    assert payload["state"] == "STOPPED"


def test_live_lifecycle_is_bounded_and_idempotently_stoppable() -> None:
    with _client() as client:
        started = client.post(
            "/live/start",
            json={"source": "binance", "symbol": "BTCUSDT"},
        )
        assert started.status_code in {200, 202}
        _assert_status_contract(started.json())
        assert started.json()["state"] in {"CONNECTING", "STREAMING", "BACKOFF"}

        stopped = client.post("/live/stop")
        stopped_again = client.post("/live/stop")

    assert stopped.status_code == 200
    assert stopped_again.status_code == 200
    _assert_status_contract(stopped_again.json())
    assert stopped_again.json()["state"] == "STOPPED"


@pytest.mark.parametrize(
    ("source", "symbol"),
    [
        ("http://169.254.169.254/latest/meta-data", "BTCUSDT"),
        ("https://user:password@example.invalid", "BTCUSDT"),
        ("file:///etc/passwd", "BTCUSDT"),
        ("binance", "../../orders"),
        ("binance", "BTCUSDT?apiKey=secret"),
        ("private-exchange", "BTCUSDT"),
    ],
)
def test_live_start_rejects_ssrf_credentials_and_untrusted_symbols(
    source: str,
    symbol: str,
) -> None:
    with _client() as client:
        response = client.post(
            "/live/start",
            json={"source": source, "symbol": symbol},
        )

    assert response.status_code in {400, 403, 422}


def test_openapi_live_surface_has_only_status_and_lifecycle_controls() -> None:
    live_paths = {
        path: methods
        for path, methods in app.openapi()["paths"].items()
        if path.startswith("/live")
    }

    assert set(live_paths) == {"/live/status", "/live/start", "/live/stop"}
    assert set(live_paths["/live/status"]) == {"get"}
    assert set(live_paths["/live/start"]) == {"post"}
    assert set(live_paths["/live/stop"]) == {"post"}

