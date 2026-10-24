"""Control-room totals: counted in the operational journal, never estimated."""

from __future__ import annotations

import json
from dataclasses import dataclass

from fastapi.testclient import TestClient

from apps.api.app import main
from apps.api.app.floor_surface import summarize


@dataclass
class _Event:
    timestamp: str
    event_type: str
    payload_json: str = "{}"

    @property
    def payload(self) -> dict:
        return json.loads(self.payload_json)


def _filled(at: str, instrument: str) -> _Event:
    return _Event(at, "ORDER_FILLED", json.dumps({"instrument_id": instrument}))


def test_totals_split_by_venue_and_brasilia_day() -> None:
    events = [
        _filled("2026-10-20T12:00:00+00:00", "BINANCE_SPOT:BTCUSDT"),
        # 02:30 UTC on the 21st is still the 20th in Brasilia (UTC-3).
        _filled("2026-10-21T02:30:00+00:00", "BINANCE_SPOT:ETHUSDT"),
        _filled("2026-10-21T13:10:00+00:00", "XP_B3:PETR4F"),
        _Event("2026-10-21T13:11:00+00:00", "ORDER_REFUSED"),
        _Event("2026-10-21T13:12:00+00:00", "PROTO_STRATEGY_SIGNAL"),
        _Event("2026-10-21T13:13:00+00:00", "TRADINGVIEW_SIGNAL_ACCEPTED"),
        _Event("2026-10-21T13:14:00+00:00", "RISK_DECISION"),
    ]
    out = summarize(events, day="2026-10-21", window=10_000)
    assert out["orders_filled_total"] == 3
    assert out["orders_filled_today"] == 1
    assert out["orders_refused_today"] == 1
    assert out["signals_today"] == 2
    assert out["orders_filled_by_venue"] == {"BINANCE_SPOT": 2, "XP_B3": 1}
    assert out["window_full"] is False and out["window_events"] == 7
    assert out["since"] == "2026-10-20T12:00:00+00:00"


def test_a_full_window_says_the_totals_cover_only_it() -> None:
    events = [_filled("2026-10-21T13:00:00+00:00", "BINANCE_SPOT:BTCUSDT")] * 3
    assert summarize(events, day="2026-10-21", window=3)["window_full"] is True


def test_the_route_is_read_only_and_labelled() -> None:
    with TestClient(main.app) as client:
        body = client.get("/v1/floor/summary").json()
        assert client.post("/v1/floor/summary").status_code == 405
    assert body["financial_connectivity"] is False and body["real_money_execution"] is False
    assert body["timezone"] == "America/Sao_Paulo"
    assert isinstance(body["orders_filled_total"], int)
