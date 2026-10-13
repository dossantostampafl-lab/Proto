from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from apps.api.app import main, signal_surface
from apps.api.app.event_state import operational_journal
from apps.api.app.settings import Settings

SECRET = "w" * 32


@pytest.fixture
def client(monkeypatch) -> TestClient:
    configured = Settings(
        tradingview_webhook_secret=SECRET,
        tradingview_ip_allowlist_enabled=False,
        operator_api_token="o" * 32,
    )
    monkeypatch.setattr(signal_surface, "settings", configured)
    monkeypatch.setattr(main, "settings", configured)
    signal_surface.reset_signal_state()
    test_client = TestClient(main.app)
    test_client.app_settings = configured  # type: ignore[attr-defined]
    yield test_client
    signal_surface.reset_signal_state()


def alert(**overrides: object) -> dict[str, object]:
    values: dict[str, object] = {
        "secret": SECRET,
        "alert_id": f"bar-{datetime.now(UTC).timestamp()}",
        "strategy_id": "tv-ema-cross",
        "instrument_id": "BINANCE_SPOT:BTCBRL",
        "direction": "LONG",
        "strength": 0.6,
        "timeframe": "15m",
        "fired_at": datetime.now(UTC).isoformat(),
    }
    values.update(overrides)
    return values


def test_webhook_accepts_alert_without_operator_token_and_grants_no_order_authority(
    client: TestClient,
) -> None:
    before = operational_journal.journal.head_sequence
    response = client.post("/v1/signals/tradingview", content=json.dumps(alert()))
    assert response.status_code == 200
    payload = response.json()
    assert payload["outcome"] == "ACCEPTED"
    assert payload["order_authority"] is False
    assert payload["real_money_execution"] is False
    events = operational_journal.journal.recent(1)
    assert operational_journal.journal.head_sequence == before + 1
    assert events[-1].event_type == "TRADINGVIEW_SIGNAL_ACCEPTED"
    assert SECRET not in events[-1].payload_json


def test_duplicate_webhook_is_acknowledged_once_in_the_journal(client: TestClient) -> None:
    body = json.dumps(alert(alert_id="dup-1"))
    first = client.post("/v1/signals/tradingview", content=body)
    before = operational_journal.journal.head_sequence
    second = client.post("/v1/signals/tradingview", content=body)
    assert second.status_code == 200
    assert second.json()["outcome"] == "DUPLICATE"
    assert second.json()["signal_id"] == first.json()["signal_id"]
    assert operational_journal.journal.head_sequence == before


def test_wrong_secret_is_rejected_with_a_stable_code(client: TestClient) -> None:
    response = client.post("/v1/signals/tradingview", content=json.dumps(alert(secret="x" * 32)))
    assert response.status_code == 401
    assert response.json()["detail"]["code"] == "unauthorized"


def test_oversized_body_is_rejected_before_parsing(client: TestClient) -> None:
    response = client.post("/v1/signals/tradingview", content=b" " * 5000)
    assert response.status_code == 413


def test_endpoint_fails_closed_when_secret_is_not_configured(monkeypatch) -> None:
    monkeypatch.setattr(signal_surface, "settings", Settings())
    signal_surface.reset_signal_state()
    response = TestClient(main.app).post("/v1/signals/tradingview", content=json.dumps(alert()))
    assert response.status_code == 503
    signal_surface.reset_signal_state()


def test_ip_allowlist_uses_the_socket_address_by_default(monkeypatch) -> None:
    configured = Settings(tradingview_webhook_secret=SECRET)
    monkeypatch.setattr(signal_surface, "settings", configured)
    signal_surface.reset_signal_state()
    response = TestClient(main.app).post(
        "/v1/signals/tradingview",
        content=json.dumps(alert()),
        headers={"X-Forwarded-For": "52.89.214.238"},
    )
    assert response.status_code == 403
    signal_surface.reset_signal_state()


def test_forwarded_address_is_used_only_when_trusted(monkeypatch) -> None:
    configured = Settings(tradingview_webhook_secret=SECRET, tradingview_trust_forwarded_for=True)
    monkeypatch.setattr(signal_surface, "settings", configured)
    signal_surface.reset_signal_state()
    response = TestClient(main.app).post(
        "/v1/signals/tradingview",
        content=json.dumps(alert()),
        headers={"X-Forwarded-For": "9.9.9.9, 52.89.214.238"},
    )
    assert response.status_code == 200
    spoofed = TestClient(main.app).post(
        "/v1/signals/tradingview",
        content=json.dumps(alert()),
        headers={"X-Forwarded-For": "52.89.214.238, 9.9.9.9"},
    )
    assert spoofed.status_code == 403
    signal_surface.reset_signal_state()


def test_recent_signals_lists_accepted_signals_newest_first(client: TestClient) -> None:
    client.post("/v1/signals/tradingview", content=json.dumps(alert(alert_id="a")))
    client.post(
        "/v1/signals/tradingview",
        content=json.dumps(alert(alert_id="b", direction="SHORT")),
    )
    payload = client.get("/v1/signals/recent").json()
    assert [item["direction"] for item in payload["signals"]][:2] == ["SHORT", "LONG"]
    assert payload["order_authority"] is False


def test_webhook_rate_limit(client: TestClient, monkeypatch) -> None:
    monkeypatch.setattr(signal_surface, "_RATE_LIMIT_PER_MINUTE", 2)
    signal_surface.reset_signal_state()
    codes = [
        client.post(
            "/v1/signals/tradingview", content=json.dumps(alert(alert_id=f"r{i}"))
        ).status_code
        for i in range(3)
    ]
    assert codes == [200, 200, 429]


def test_other_mutating_routes_still_require_the_operator_token(client: TestClient) -> None:
    assert client.post("/paper/start").status_code == 401


def test_stale_alert_is_rejected(client: TestClient) -> None:
    stale = (datetime.now(UTC) - timedelta(minutes=5)).isoformat()
    response = client.post("/v1/signals/tradingview", content=json.dumps(alert(fired_at=stale)))
    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "stale_alert"


def test_production_requires_a_strong_secret_and_the_ip_allowlist() -> None:
    production = {"app_env": "production", "operator_api_token": "o" * 32}
    with pytest.raises(ValueError, match="TRADINGVIEW_WEBHOOK_SECRET"):
        Settings(**production, tradingview_webhook_secret="short")
    with pytest.raises(ValueError, match="TRADINGVIEW_IP_ALLOWLIST_ENABLED"):
        Settings(**production, tradingview_ip_allowlist_enabled=False)
    assert Settings(**production, tradingview_webhook_secret=SECRET).tradingview_webhook_secret


def test_instrument_allowlist_is_parsed_and_normalized() -> None:
    configured = Settings(tradingview_instruments=" binance_spot:btcbrl , ,XP_B3:PETR4")
    assert configured.tradingview_instrument_allowlist == frozenset(
        {"BINANCE_SPOT:BTCBRL", "XP_B3:PETR4"}
    )


def test_signal_is_not_accepted_when_the_journal_cannot_record_it(
    client: TestClient, monkeypatch
) -> None:
    from apps.api.app import event_state

    async def unavailable(**_kwargs):
        raise event_state.JournalUnavailable("database is down")

    monkeypatch.setattr(signal_surface, "record_operational_event", unavailable)
    body = json.dumps(alert(alert_id="durable-1"))
    response = client.post("/v1/signals/tradingview", content=body)
    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "journal_unavailable"
    assert client.get("/v1/signals/recent").json()["signals"] == []

    monkeypatch.undo()
    monkeypatch.setattr(signal_surface, "settings", client.app_settings)
    retry = client.post("/v1/signals/tradingview", content=body)
    assert retry.json()["outcome"] == "ACCEPTED"
