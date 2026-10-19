"""The autonomy loop: own strategy, proxy-aware TradingView source address, the closed-loop
drill and the retired paper autopilot."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace

from fastapi.testclient import TestClient
from starlette.requests import Request

from apps.api.app import floor_surface, main, signal_surface, strategy_surface
from apps.api.app.settings import Settings
from services.execution import SignalDirection, SignalSource
from services.execution.venue import Quote
from services.signals.proto_strategy import STRATEGY_ID, EmaCrossPolicy, EmaCrossStrategy

T0 = datetime(2026, 10, 16, 12, 0, tzinfo=UTC)
BTC = "BINANCE_SPOT:BTCUSDT"


def feed(strategy: EmaCrossStrategy, prices: list[float], *, start: datetime = T0):
    signals = []
    for minute, price in enumerate(prices):
        at = start + timedelta(minutes=minute, seconds=30)
        if (signal := strategy.observe(BTC, price, at)) is not None:
            signals.append(signal)
    return signals


# ---------- own strategy ----------


def test_a_strong_upward_cross_emits_one_long_signal_with_no_order_authority() -> None:
    strategy = EmaCrossStrategy()
    flat_then_down = [100.0] * 15 + [100 - i * 0.2 for i in range(1, 11)]
    up = [flat_then_down[-1] + i * 0.6 for i in range(1, 12)]
    signals = feed(strategy, flat_then_down + up)
    longs = [s for s in signals if s.direction is SignalDirection.LONG]
    assert len(longs) == 1
    signal = longs[0]
    assert signal.source is SignalSource.PROTO_STRATEGY and signal.strategy_id == STRATEGY_ID
    assert signal.instrument_id == BTC and 0 < signal.strength <= 1
    assert signal.received_at >= signal.observed_at
    assert not hasattr(signal, "quantity")  # an opinion, never an order


def test_a_cross_waits_for_the_gap_to_open_before_signalling() -> None:
    """The cross bar alone is too weak; the signal comes once the trend has opened the gap."""

    strategy = EmaCrossStrategy()
    base = [100.0] * 25
    # A gentle climb: crosses on the first bar, the gap only reaches entry strength later.
    climb = [100 + 0.03 * i for i in range(1, 15)]
    signals = feed(strategy, base + climb)
    assert len(signals) == 1 and signals[0].direction is SignalDirection.LONG
    assert signals[0].strength >= 0.6
    assert signals[0].observed_at > T0 + timedelta(minutes=26)  # not the cross bar itself


def test_a_cross_that_reverses_or_never_opens_up_emits_nothing() -> None:
    reversal = [100.0] * 25 + [100.01, 99.99, 99.98, 99.97]
    assert [s for s in feed(EmaCrossStrategy(), reversal) if s.direction.value == "LONG"] == []
    drift = [100.0] * 25 + [100 + 0.001 * i for i in range(1, 30)]
    assert feed(EmaCrossStrategy(), drift) == []


def test_no_signal_before_enough_bars_or_on_a_flat_market() -> None:
    strategy = EmaCrossStrategy()
    assert feed(strategy, [100.0] * 60) == []
    assert EmaCrossStrategy().observe(BTC, 0.0, T0) is None


def test_the_cooldown_stops_a_flapping_market_from_signalling_every_bar() -> None:
    strategy = EmaCrossStrategy(policy=EmaCrossPolicy(cooldown=timedelta(hours=1)))
    zigzag = [100.0] * 22 + [100 + (3 if i % 2 else -3) for i in range(30)]
    assert len(feed(strategy, zigzag)) <= 1


def test_signal_ids_are_deterministic_so_a_replay_is_journaled_once() -> None:
    prices = (
        [100.0] * 15 + [100 - i * 0.2 for i in range(1, 11)] + [98 + i * 0.6 for i in range(1, 12)]
    )
    first = [s.signal_id for s in feed(EmaCrossStrategy(), prices)]
    assert first and first == [s.signal_id for s in feed(EmaCrossStrategy(), prices)]


def test_the_strategy_surface_feeds_only_fresh_quotes_and_hands_signals_to_the_agents(
    monkeypatch,
) -> None:
    strategy_surface.reset_strategy_state()
    now = T0
    quotes = {
        BTC: Quote(bid=Decimal("100"), ask=Decimal("100.2"), observed_at=now - timedelta(seconds=2))
    }
    monkeypatch.setattr(strategy_surface, "live_quote", lambda i, market_source: quotes.get(i))
    accepted = []

    async def accept(signal, *, event_type, source):
        accepted.append((signal, event_type, source))
        return True

    monkeypatch.setattr(strategy_surface, "accept_internal_signal", accept)
    emitted = SimpleNamespace(signal_id="x")
    monkeypatch.setattr(strategy_surface.strategy, "observe", lambda i, mid, at: emitted)
    assert asyncio.run(strategy_surface.tick(now)) == 1
    assert accepted == [(emitted, "PROTO_STRATEGY_SIGNAL", "proto-strategy")]
    # A stale quote feeds nothing.
    quotes[BTC] = Quote(
        bid=Decimal("100"), ask=Decimal("100.2"), observed_at=now - timedelta(minutes=1)
    )
    assert asyncio.run(strategy_surface.tick(now)) == 0
    strategy_surface.reset_strategy_state()


# ---------- TradingView behind the local reverse proxy ----------


def _request(peer: str, forwarded: str | None) -> Request:
    headers = [(b"x-forwarded-for", forwarded.encode())] if forwarded else []
    return Request({"type": "http", "client": (peer, 1234), "headers": headers})


def test_behind_the_local_proxy_the_alert_source_is_the_last_forwarded_hop(monkeypatch) -> None:
    monkeypatch.setattr(signal_surface, "settings", Settings(tradingview_trust_forwarded_for=False))
    assert (
        signal_surface._client_ip(_request("127.0.0.1", "6.6.6.6, 52.89.214.238"))
        == "52.89.214.238"
    )
    # A direct (non-loopback) peer can never claim an address through the header.
    assert signal_surface._client_ip(_request("10.20.2.11", "52.89.214.238")) == "10.20.2.11"
    assert signal_surface._client_ip(_request("127.0.0.1", None)) == "127.0.0.1"


# ---------- closed-loop drill (the drill itself: tests/unit/test_e2e_drill.py) ----------


def test_the_floor_shows_strategy_signals_and_the_drill_verdict() -> None:
    event = {
        "sequence": 1,
        "timestamp": T0.isoformat(),
        "event_type": "PROTO_STRATEGY_SIGNAL",
        "payload": {"signal": {"direction": "LONG", "strength": 0.8, "instrument_id": BTC}},
    }
    assert floor_surface.floor_actions(event)[0]["station"] == "tradingview"
    done = {
        "sequence": 2,
        "timestamp": T0.isoformat(),
        "event_type": "E2E_DRILL_COMPLETED",
        "payload": {"verdict": "FAILED", "instrument_id": BTC},
    }
    action = floor_surface.floor_actions(done)[0]
    assert action["station"] == "reconciliation" and action["tone"] == "alert"


# ---------- retired paper autopilot ----------


def test_the_old_paper_autopilot_can_no_longer_start(monkeypatch) -> None:
    monkeypatch.setattr(main, "settings", Settings())
    with TestClient(main.app) as client:
        response = client.post(
            "/paper/automation/start",
            json={
                "symbol": "BTC",
                "imbalance_trigger": 0.65,
                "cooldown_seconds": 20,
                "quantity": 0.001,
                "max_spread_bps": 20,
                "stop_loss_fraction": 0.02,
            },
        )
    assert response.status_code == 410
    assert "retired" in response.json()["detail"]


def test_the_image_turns_the_loop_on_and_reads_binance_quotes() -> None:
    from pathlib import Path

    dockerfile = (Path(__file__).resolve().parents[2] / "Dockerfile").read_text()
    for line in (
        "LIVE_MARKET_SOURCE=BINANCE",
        "AGENT_PIPELINE_ENABLED=true",
        "PROTO_STRATEGY_ENABLED=true",
        "E2E_DRILL_ON_START=true",
        "AGENT_ROSTER_ENABLED=true",
        # TradingView alerts name the pairs that have live quotes (USDT, not BRL).
        "TRADINGVIEW_INSTRUMENTS=BINANCE_SPOT:BTCUSDT,BINANCE_SPOT:ETHUSDT,BINANCE_SPOT:SOLUSDT",
    ):
        assert line in dockerfile
    assert "REAL_MONEY" not in dockerfile and "LIVE_TRADING" not in dockerfile
