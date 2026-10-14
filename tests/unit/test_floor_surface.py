"""Trading floor: journal events become live station actions."""

from __future__ import annotations

import json
import time
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from apps.api.app import agent_surface, main, oms_surface, risk_surface, signal_surface
from apps.api.app.floor_surface import STATIONS, floor_actions
from apps.api.app.settings import Settings

SECRET = "w" * 32


def event(kind: str, payload: dict, seq: int = 1) -> dict:
    return {
        "sequence": seq,
        "timestamp": "2026-10-14T12:00:00Z",
        "event_type": kind,
        "payload": payload,
    }


def test_each_event_type_maps_to_its_station() -> None:
    signal = floor_actions(
        event(
            "TRADINGVIEW_SIGNAL_ACCEPTED",
            {
                "signal": {
                    "direction": "LONG",
                    "strength": 0.9,
                    "instrument_id": "BINANCE_SPOT:BTCUSDT",
                }
            },
        )
    )
    assert [(a["station"], a["tone"], a["symbol"]) for a in signal] == [
        ("tradingview", "ok", "BTCUSDT")
    ]

    decision = floor_actions(
        event(
            "AGENT_DECISION",
            {
                "instrument_id": "BINANCE_SPOT:BTCUSDT",
                "outcome": "REDUCED",
                "fusion": {"direction": "LONG", "score": 0.8},
                "review": {
                    "verdict": "REDUCE",
                    "reviewer": "claude",
                    "rationale": "wide  spread\n",
                },
            },
        )
    )
    assert [a["station"] for a in decision] == ["fusion", "reviewer"]
    assert decision[1]["tone"] == "warn"
    assert decision[1]["headline"] == "REDUCE (claude): wide spread"

    rejected = floor_actions(
        event(
            "RISK_DECISION",
            {
                "approved": False,
                "rejections": ["fresh_quote", "price_collar"],
                "permit_id": None,
                "instrument_id": "BINANCE_SPOT:BTCUSDT",
            },
        )
    )
    assert rejected[0]["tone"] == "alert" and "fresh_quote" in rejected[0]["headline"]

    order = floor_actions(
        event(
            "ORDER_FILLED",
            {"side": "BUY", "quantity": "0.01", "instrument_id": "BINANCE_SPOT:BTCUSDT"},
        )
    )
    assert (order[0]["station"], order[0]["tone"]) == ("oms", "ok")
    unknown = floor_actions(event("ORDER_DISPATCH_UNKNOWN", {"intent_id": "x"}))
    assert unknown[0]["tone"] == "alert"

    divergence = floor_actions(
        event("RECONCILIATION_DIVERGENCE", {"divergences": [{"asset": "USDT"}, {"asset": "BTC"}]})
    )
    assert divergence[0]["headline"] == "divergence: BTC, USDT"
    assert floor_actions(event("SOMETHING_ELSE", {})) == []
    assert floor_actions(event("ORDER_FILLED", {"reason": "x" * 500}))[0]["headline"].endswith("…")


@pytest.fixture
def configured(monkeypatch):
    values = Settings(
        tradingview_webhook_secret=SECRET,
        tradingview_ip_allowlist_enabled=False,
        tradingview_instruments="BINANCE_SPOT:BTCUSDT",
        live_market_source="BINANCE",
        agent_pipeline_enabled=True,
        oms_paper_execution_enabled=True,
        oms_paper_slippage_bps=0.0,
    )
    for module in (main, signal_surface, agent_surface, risk_surface, oms_surface):
        monkeypatch.setattr(module, "settings", values)
    from apps.api.app import floor_surface

    monkeypatch.setattr(floor_surface, "settings", values)
    monkeypatch.setattr(
        agent_surface.live_monitor,
        "snapshot",
        lambda symbol: {
            "symbol": symbol,
            "venue": "BINANCE",
            "bid": 62_000.00,
            "ask": 62_000.20,
            "timestamp": (datetime.now(UTC) - timedelta(seconds=1)).isoformat(),
        },
    )
    resets = (
        signal_surface.reset_signal_state,
        agent_surface.reset_agent_state,
        risk_surface.reset_risk_state,
        oms_surface.reset_oms_state,
    )
    for reset in resets:
        reset()
    yield values
    for reset in resets:
        reset()


def alert() -> str:
    return json.dumps(
        {
            "secret": SECRET,
            "alert_id": f"a-{time.time_ns()}",
            "strategy_id": "tv-ema",
            "instrument_id": "BINANCE_SPOT:BTCUSDT",
            "direction": "LONG",
            "strength": 0.9,
            "timeframe": "15",
            "fired_at": datetime.now(UTC).isoformat(),
        }
    )


def test_floor_streams_the_whole_chain_live(configured) -> None:
    with TestClient(main.app) as client, client.websocket_connect("/ws/floor") as ws:
        assert ws.receive_json()["type"] == "subscribed"
        assert client.post("/v1/signals/tradingview", content=alert()).status_code == 200
        seen: list[str] = []
        while "oms" not in seen or seen.count("oms") < 2:
            message = ws.receive_json()
            assert message["type"] == "floor"
            seen.append(message["data"]["station"])
            assert len(seen) < 20
        assert seen[:4] == ["tradingview", "fusion", "reviewer", "risk"]

        state = client.get("/v1/floor/state").json()
    assert state["data_mode"] == "PAPER"
    assert set(state["stations"]) == set(STATIONS)
    assert state["stations"]["oms"]["headline"].startswith("BUY")
    assert state["stations"]["risk"]["headline"] == "permit issued"
    assert state["accounts"]["USDT"]["positions"]
    assert state["real_money_execution"] is False
    text = json.dumps(state).lower()
    assert "signature" not in text and SECRET not in text


async def test_floor_state_shows_each_scope_and_what_blocks_it(configured) -> None:
    from apps.api.app import promotion_surface
    from apps.api.app.floor_surface import floor_state
    from services.execution import ReleaseStage
    from services.promotion import EvidenceKind, Scope

    promotion_surface.reset_promotion_state()
    scope = Scope("paper-binance-spot", "BINANCE_SPOT", "hybrid-fusion-v1", "fusion-v1", "paper")
    registry = promotion_surface.registry()
    await registry.promote(scope, ReleaseStage.REPLAY, operator=False, by="t")
    state = await floor_state()
    await registry.add_evidence(
        scope, EvidenceKind.REPLAY_REPRODUCIBLE, reference="ci#1", passed=True, by="t"
    )
    ready = await floor_state()
    promotion_surface.reset_promotion_state()

    [entry] = state["promotion"]
    assert entry["stage"] == "REPLAY" and entry["next_stage"] == "SHADOW"
    assert entry["blockers"] == ["missing REPLAY_REPRODUCIBLE evidence recorded in REPLAY"]
    assert ready["promotion"][0]["blockers"] == []
