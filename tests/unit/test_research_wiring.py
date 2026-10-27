"""Lab strategies behind the roster flag: signals only, off by default, current behavior kept."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from services.agents import roster as roster_module
from services.agents.roster import (
    LAB_PARAM_LADDER,
    AgentRoster,
    Slot,
    SlotKind,
    params_for,
    slot_of,
)
from services.execution import Signal, SignalDirection, SignalSource
from services.research.data import Bar
from services.research.live import LAB_STRATEGY_PREFIX, LabSignalStrategy, lab_strategy_id
from services.research.strategies import build

BTC = "BINANCE_SPOT:BTCUSDT"
SOL = "BINANCE_SPOT:SOLUSDT"
SPEC = {"strategy": "ts_momentum", "lookback": 3, "vol_period": 5, "atr_period": 3}
LADDER = {BTC: ({"spec": SPEC, "timeframe_minutes": 1},)}
START = datetime(2026, 1, 1, tzinfo=UTC)


def ema_signal(strategy_id: str = roster_module.EMA_STRATEGY_ID) -> Signal:
    return Signal(
        signal_id=uuid4(),
        source=SignalSource.PROTO_STRATEGY,
        strategy_id=strategy_id,
        instrument_id=BTC,
        direction=SignalDirection.LONG,
        strength=0.9,
        timeframe="1m",
        observed_at=START,
        received_at=START,
    )


def test_default_params_are_unchanged() -> None:
    assert params_for(SlotKind.EMA, 0) == {"fast": 5, "slow": 20}
    assert params_for(SlotKind.EMA, 0, instrument_id=BTC) == {"fast": 5, "slow": 20}
    assert params_for(SlotKind.TRADINGVIEW, 1, instrument_id=BTC, lab_strategies=True) == {
        "min_strength": "0.2"
    }


def test_every_ladder_spec_builds_and_keeps_a_protective_exit() -> None:
    for instrument, ladder in LAB_PARAM_LADDER.items():
        assert instrument.startswith("BINANCE_SPOT:") and ladder
        for entry in ladder:
            strategy = build(entry["spec"])
            assert entry["timeframe_minutes"] in (1, 5, 15, 60, 240)
            assert lab_strategy_id(entry["spec"]).startswith(LAB_STRATEGY_PREFIX)
            assert strategy.describe()


def test_lab_params_only_with_the_flag(monkeypatch) -> None:
    monkeypatch.setattr(roster_module, "LAB_PARAM_LADDER", LADDER)
    lab = params_for(SlotKind.EMA, 0, instrument_id=BTC, lab_strategies=True)
    assert lab["lab_strategy"] == lab_strategy_id(SPEC) and lab["spec"] == SPEC
    assert params_for(SlotKind.EMA, 0, instrument_id=BTC) == {"fast": 5, "slow": 20}
    # An instrument without a ladder keeps the EMA even with the flag.
    assert params_for(SlotKind.EMA, 0, instrument_id=SOL, lab_strategies=True) == {
        "fast": 5,
        "slow": 20,
    }


async def test_a_lab_agent_accepts_only_its_own_strategy(monkeypatch) -> None:
    monkeypatch.setattr(roster_module, "LAB_PARAM_LADDER", LADDER)
    lab_roster = AgentRoster(lab_strategies=True)
    await lab_roster.seed()
    agent = lab_roster.active_for(Slot(SlotKind.EMA, BTC))
    assert agent is not None and agent.params["lab_strategy"] == lab_strategy_id(SPEC)
    lab_signal = ema_signal(lab_strategy_id(SPEC))
    assert slot_of(lab_signal) == Slot(SlotKind.EMA, BTC)
    assert agent.accepts(lab_signal)
    assert not agent.accepts(ema_signal())
    assert not agent.accepts(ema_signal(f"{LAB_STRATEGY_PREFIX}other-00000000"))

    plain = AgentRoster()
    await plain.seed()
    ema_agent = plain.active_for(Slot(SlotKind.EMA, BTC))
    assert ema_agent is not None and ema_agent.params == {"fast": 5, "slow": 20}
    assert ema_agent.accepts(ema_signal()) and not ema_agent.accepts(lab_signal)


def test_live_adapter_emits_long_then_a_protective_short() -> None:
    lab = LabSignalStrategy(SPEC, timeframe_minutes=1)
    signals = []
    price = 100.0
    for minute in range(12):
        price *= 1.01
        for second in (0, 30):
            at = START + timedelta(minutes=minute, seconds=second)
            if (s := lab.observe(BTC, price, at)) is not None:
                signals.append(s)
    assert [s.direction for s in signals] == [SignalDirection.LONG]
    long = signals[0]
    assert long.strategy_id == lab.strategy_id and long.source is SignalSource.PROTO_STRATEGY
    assert long.timeframe == "1m" and 0 < long.strength <= 1
    assert long.observed_at <= long.received_at
    crash = lab.observe(BTC, price * 0.5, START + timedelta(minutes=11, seconds=45))
    assert crash is not None and crash.direction is SignalDirection.SHORT
    assert lab.status()[BTC]["holding"] is False


def test_live_adapter_rejects_bad_input() -> None:
    with pytest.raises(ValueError):
        LabSignalStrategy(SPEC, timeframe_minutes=0)
    assert LabSignalStrategy(SPEC, timeframe_minutes=60).observe(BTC, 0.0, START) is None


def test_live_adapter_ignores_non_finite_mids() -> None:
    lab = LabSignalStrategy(SPEC, timeframe_minutes=1)
    for bad in (float("inf"), float("nan"), -5.0):
        assert lab.observe(BTC, bad, START) is None
    assert lab.status() == {}


def _rising(lab: LabSignalStrategy, minutes: int) -> float:
    price = 100.0
    for minute in range(minutes):
        price *= 1.01
        assert lab.observe(BTC, price, START + timedelta(minutes=minute, seconds=30)) is None
    return price


def test_live_adapter_decides_nothing_on_a_bar_closed_after_a_feed_gap() -> None:
    contiguous = LabSignalStrategy(SPEC, timeframe_minutes=1)
    price = _rising(contiguous, 6)
    fresh = contiguous.observe(BTC, price * 1.01, START + timedelta(minutes=6, seconds=30))
    assert fresh is not None and fresh.direction is SignalDirection.LONG

    gapped = LabSignalStrategy(SPEC, timeframe_minutes=1)
    price = _rising(gapped, 6)
    late = START + timedelta(minutes=40, seconds=30)  # the quotes stopped for half an hour
    assert gapped.observe(BTC, price * 1.01, late) is None
    assert gapped.status()[BTC] == {"bars": 6, "holding": False}


def _kline(minute: int, close: float) -> Bar:
    t = int((START + timedelta(minutes=minute)).timestamp() * 1000)
    return Bar(t, close, close, close, close, 1.0)


def test_warm_up_never_duplicates_or_reorders_history() -> None:
    lab = LabSignalStrategy(SPEC, timeframe_minutes=1, history=10)
    lab.warm(BTC, [_kline(m, 100.0 + m) for m in (3, 1, 2, 2)])
    assert [b.open_time for b in lab._books[BTC].bars] == [
        _kline(m, 0).open_time for m in (1, 2, 3)
    ]
    for minute in (5, 6, 7):  # live mids: bar 5 and 6 close
        lab.observe(BTC, 110.0, START + timedelta(minutes=minute, seconds=10))
    # A late warm-up returns overlapping klines (and the bar still forming): only older ones land.
    lab.warm(BTC, [_kline(m, 1.0) for m in range(0, 9)])
    times = [b.open_time for b in lab._books[BTC].bars]
    assert times == sorted(set(times))
    assert times == [_kline(m, 0).open_time for m in (0, 1, 2, 3, 5, 6)]


def test_lab_strategies_refuse_to_start_without_the_warm_up() -> None:
    from apps.api.app.settings import Settings

    with pytest.raises(ValueError, match="AGENT_LAB_WARMUP_KLINES"):
        Settings(agent_lab_strategies_enabled=True)
    assert Settings(agent_lab_strategies_enabled=True, agent_lab_warmup_klines=True)
    assert Settings(agent_lab_warmup_klines=True)  # warm-up alone changes nothing
