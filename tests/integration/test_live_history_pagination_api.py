from datetime import UTC, datetime

from fastapi.testclient import TestClient

from apps.api.app.live_app import app
from apps.api.app.live_history_metrics import live_history_read_metrics
from apps.api.app.live_monitor import live_monitor
from services.market_data import (
    LiveHistoryCursorError,
    LiveTickJournalError,
    MarketTick,
    PersistedLiveTick,
    PersistedLiveTickPage,
)


def _page() -> PersistedLiveTickPage:
    now = datetime.now(UTC)
    tick = MarketTick(
        timestamp=now,
        venue="coinbase-public",
        symbol="BTC",
        bid=60_000.0,
        ask=60_001.0,
        last=60_000.5,
        volume=100.0,
        bid_size=1.0,
        ask_size=1.0,
        sequence=7,
    )
    return PersistedLiveTickPage(
        items=(
            PersistedLiveTick(
                tick=tick,
                received_at=now,
                connection_generation=2,
                persisted_at=now,
            ),
        ),
        next_cursor="opaque-next-cursor",
    )


def test_history_api_returns_read_only_page_metadata(monkeypatch) -> None:
    async def history_page(**_: object) -> PersistedLiveTickPage:
        return _page()

    live_history_read_metrics.reset()
    monkeypatch.setattr(live_monitor, "persisted_history_page", history_page)
    with TestClient(app) as client:
        response = client.get(
            "/live/history/BTC",
            params={
                "limit": 1,
                "start_at": "2026-08-29T20:00:00-03:00",
                "end_at": "2026-08-29T21:00:00-03:00",
            },
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["source"] == "PUBLIC_READ_ONLY_PERSISTED"
    assert payload["count"] == 1
    assert payload["next_cursor"] == "opaque-next-cursor"
    assert payload["has_more"] is True
    assert payload["financial_connectivity"] is False
    assert payload["real_money_execution"] is False
    assert "no-store" in response.headers["cache-control"]

    observed = live_history_read_metrics.snapshot()
    assert observed["requests_total"] == 1
    assert observed["successes_total"] == 1
    assert observed["rows_returned_total"] == 1
    assert observed["pages_with_more_total"] == 1
    assert observed["cursor_rejections_total"] == 0
    assert observed["backend_failures_total"] == 0


def test_history_api_rejects_invalid_cursor_without_marking_backend_failure(monkeypatch) -> None:
    async def history_page(**_: object) -> PersistedLiveTickPage:
        raise LiveHistoryCursorError("bad cursor")

    live_history_read_metrics.reset()
    monkeypatch.setattr(live_monitor, "persisted_history_page", history_page)
    with TestClient(app) as client:
        response = client.get("/live/history/BTC", params={"cursor": "bad"})

    assert response.status_code == 422
    assert response.json()["detail"] == "history cursor is invalid"
    assert "no-store" in response.headers["cache-control"]

    observed = live_history_read_metrics.snapshot()
    assert observed["requests_total"] == 1
    assert observed["cursor_rejections_total"] == 1
    assert observed["backend_failures_total"] == 0
    assert observed["successes_total"] == 0


def test_history_api_counts_backend_failure_separately(monkeypatch) -> None:
    async def history_page(**_: object) -> PersistedLiveTickPage:
        raise LiveTickJournalError("database unavailable")

    live_history_read_metrics.reset()
    monkeypatch.setattr(live_monitor, "persisted_history_page", history_page)
    with TestClient(app) as client:
        response = client.get("/live/history/BTC")

    assert response.status_code == 503
    assert response.headers["retry-after"] == "1"
    observed = live_history_read_metrics.snapshot()
    assert observed["requests_total"] == 1
    assert observed["backend_failures_total"] == 1
    assert observed["cursor_rejections_total"] == 0
    assert observed["successes_total"] == 0


def test_history_api_requires_timezone_aware_bounds() -> None:
    with TestClient(app) as client:
        response = client.get(
            "/live/history/BTC",
            params={"start_at": "2026-08-29T20:00:00"},
        )

    assert response.status_code == 422
    assert response.json()["detail"] == "start_at must include a timezone offset"


def test_history_api_rejects_reversed_time_window() -> None:
    with TestClient(app) as client:
        response = client.get(
            "/live/history/BTC",
            params={
                "start_at": "2026-08-29T22:00:00Z",
                "end_at": "2026-08-29T21:00:00Z",
            },
        )

    assert response.status_code == 422
    assert response.json()["detail"] == "start_at must not be after end_at"
