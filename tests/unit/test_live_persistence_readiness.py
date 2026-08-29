from fastapi import Response
from pytest import MonkeyPatch

from apps.api.app.live_monitor import live_monitor
from apps.api.app.live_routes import live_ready


def test_live_readiness_fails_closed_when_required_persistence_is_unhealthy(
    monkeypatch: MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        live_monitor,
        "status",
        lambda: {
            "running": True,
            "receiving_data": True,
            "complete": True,
            "all_symbols_fresh": True,
            "all_symbols_receipts_fresh": True,
            "all_symbols_current_connection": True,
            "missing_receipt_symbols": [],
            "feed_health": {
                "connected": True,
                "message_fresh": True,
                "consecutive_parse_errors": 0,
            },
            "persistence": {
                "configured": True,
                "required": True,
                "healthy": False,
            },
            "financial_connectivity": False,
            "real_money_execution": False,
        },
    )
    response = Response()

    payload = live_ready(response)

    assert response.status_code == 503
    assert response.headers["retry-after"] == "1"
    assert payload["status"] == "not_ready"
    assert payload["readiness_failures"] == ["PERSISTENCE_UNAVAILABLE"]
    assert payload["financial_connectivity"] is False
    assert payload["real_money_execution"] is False
