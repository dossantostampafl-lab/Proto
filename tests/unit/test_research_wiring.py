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
