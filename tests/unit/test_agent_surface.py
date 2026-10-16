from __future__ import annotations

import json
import time
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from apps.api.app import agent_surface, main, risk_surface, signal_surface
from apps.api.app.event_state import operational_journal
from apps.api.app.settings import Settings
from services.agents import DeterministicReviewer

SECRET = "w" * 32


def configure(monkeypatch, **overrides) -> Settings:
    values = {
        "tradingview_webhook_secret": SECRET,
        "tradingview_ip_allowlist_enabled": False,
        "tradingview_instruments": "BINANCE_SPOT:BTCUSDT,BINANCE_SPOT:BTCBRL",
        "live_market_source": "BINANCE",
        "agent_pipeline_enabled": True,
    }
    values.update(overrides)
    configured = Settings(**values)
    for module in (main, signal_surface, agent_surface, risk_surface):
        monkeypatch.setattr(module, "settings", configured)
    signal_surface.reset_signal_state()
    agent_surface.reset_agent_state()
    risk_surface.reset_risk_state()
    return configured


@pytest.fixture(autouse=True)
def _reset():
    yield
    signal_surface.reset_signal_state()
    agent_surface.reset_agent_state()
    risk_surface.reset_risk_state()


def live_snapshot(symbol: str, *, venue: str = "binance-public-spot", age: float = 1.0):
    return {
        "symbol": symbol,
        "venue": venue,
        "bid": 62_000.10,
        "ask": 62_000.30,
        "timestamp": (datetime.now(UTC) - timedelta(seconds=age)).isoformat(),
    }


def wait_for_decisions(client: TestClient, count: int) -> list[dict]:
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        decisions = client.get("/v1/agents/decisions/recent").json()["decisions"]
        if len(decisions) >= count:
            return decisions
        time.sleep(0.01)
    raise AssertionError("agent decisions did not arrive")


def alert(instrument_id: str, **overrides) -> str:
    values = {
        "secret": SECRET,
        "alert_id": f"a-{datetime.now(UTC).timestamp()}",
        "strategy_id": "tv-ema",
        "instrument_id": instrument_id,
        "direction": "LONG",
        "strength": 0.9,
        "timeframe": "15",
        "fired_at": datetime.now(UTC).isoformat(),
    }
    values.update(overrides)
    return json.dumps(values)


def test_accepted_signal_triggers_a_journaled_paper_proposal(monkeypatch) -> None:
    configure(monkeypatch)
    monkeypatch.setattr(agent_surface.live_monitor, "snapshot", live_snapshot)
    with TestClient(main.app) as client:
        before = operational_journal.journal.head_sequence
        response = client.post("/v1/signals/tradingview", content=alert("BINANCE_SPOT:BTCUSDT"))
        assert response.status_code == 200
        decisions = wait_for_decisions(client, 1)
    assert len(decisions) == 1
    decision = decisions[0]
    assert decision["outcome"] == "APPROVED"
    assert decision["proposal"]["side"] == "BUY"
    assert decision["proposal"]["stage"] == "PAPER"
    assert decision["order_authority"] is False
    events = [e.event_type for e in operational_journal.journal.recent(3)]
    assert events == ["TRADINGVIEW_SIGNAL_ACCEPTED", "AGENT_DECISION", "RISK_DECISION"]
    assert operational_journal.journal.head_sequence == before + 3
    risk = operational_journal.journal.recent(1)[0].payload
    assert risk["approved"] is True
    assert risk["intent_fingerprint"] == decision["proposal_fingerprint"]
    assert "signature" not in json.dumps(risk)


def test_brl_pairs_abstain_without_a_brl_quote(monkeypatch) -> None:
    configure(monkeypatch)
    with TestClient(main.app) as client:
        client.post("/v1/signals/tradingview", content=alert("BINANCE_SPOT:BTCBRL"))
        decision = wait_for_decisions(client, 1)[0]
    assert decision["outcome"] == "ABSTAINED"
    assert decision["abstain_reason"] == "no market data"


def test_disabled_pipeline_only_buffers_signals(monkeypatch) -> None:
    configure(monkeypatch, agent_pipeline_enabled=False)
    with TestClient(main.app) as client:
        client.post("/v1/signals/tradingview", content=alert("BINANCE_SPOT:BTCUSDT"))
        status = client.get("/v1/agents/status").json()
    assert status["enabled"] is False
    assert status["buffered_signals"] == 1
    assert status["decisions"] == 0
    assert status["real_money_execution"] is False


def test_live_quote_requires_a_binance_usdt_stream(monkeypatch) -> None:
    configure(monkeypatch)
    monkeypatch.setattr(agent_surface.live_monitor, "snapshot", live_snapshot)
    assert agent_surface.live_quote("BINANCE_SPOT:BTCUSDT") is not None
    assert agent_surface.live_quote("BINANCE_SPOT:BTCBRL") is None
    monkeypatch.setattr(
        agent_surface.live_monitor, "snapshot", lambda s: live_snapshot(s, venue="coinbase-public")
    )
    assert agent_surface.live_quote("BINANCE_SPOT:BTCUSDT") is None
    configure(monkeypatch, live_market_source="COINBASE")
    assert agent_surface.live_quote("BINANCE_SPOT:BTCUSDT") is None


def test_operator_evaluate_requires_the_token_and_a_known_instrument(monkeypatch) -> None:
    configure(monkeypatch, operator_api_token="o" * 32)
    client = TestClient(main.app)
    body = {"instrument_id": "BINANCE_SPOT:BTCUSDT"}
    assert client.post("/v1/agents/evaluate", json=body).status_code == 401
    headers = {"X-Proto-Operator-Token": "o" * 32}
    ok = client.post("/v1/agents/evaluate", json=body, headers=headers)
    assert ok.status_code == 200
    assert ok.json()["decision"]["outcome"] == "NO_TRADE"
    unknown = client.post("/v1/agents/evaluate", json={"instrument_id": "X:Y"}, headers=headers)
    assert unknown.status_code == 422


def test_reviewer_selection_never_silently_approves(monkeypatch) -> None:
    configure(monkeypatch)
    assert isinstance(agent_surface._reviewer(), DeterministicReviewer)

    configure(monkeypatch, agent_reviewer="claude")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key-not-used")
    reviewer = agent_surface._reviewer()
    assert reviewer.name == "claude"
    assert reviewer.model == "claude-opus-5-5"

    import anthropic

    def broken(**_kwargs):
        raise RuntimeError("no credentials")

    monkeypatch.setattr(anthropic, "AsyncAnthropic", broken)
    assert agent_surface._reviewer().name == "claude-unavailable"


async def test_unavailable_reviewer_defers() -> None:
    reviewer = agent_surface.UnavailableReviewer("AI reviewer unavailable: RuntimeError")
    result = await reviewer.review(None)  # type: ignore[arg-type]
    assert result.verdict.value == "DEFER"


def test_agent_settings_are_validated() -> None:
    with pytest.raises(ValueError, match="agent_reviewer"):
        Settings(agent_reviewer="gpt")
    with pytest.raises(ValueError, match="agent_effort"):
        Settings(agent_effort="extreme")
