"""Strategy lab: backtester math, walk-forward splitting, indicators and strategies (offline)."""

from __future__ import annotations

import json
import math
import random
from datetime import UTC, datetime, timedelta

import pytest

from services.execution import SignalDirection
from services.research import indicators as ind
from services.research.backtest import (
    CostModel,
    Result,
    SizingModel,
    Trade,
    buy_and_hold,
    metrics,
    run,
)
from services.research.data import (
    Bar,
    check_gaps,
    download,
    load,
    parse_klines,
    resample,
    save,
)
from services.research.lab import grid, label, main, render
from services.research.strategies import (
    ConnorsRsi,
    Donchian,
    EmaCross,
    EmaTrend,
    Plan,
    RandomEntry,
    RegimeFilter,
    TsMomentum,
    build,
)
from services.research.walkforward import (
    deflated_sharpe,
    expected_max_sharpe,
    walk_forward,
    windows,
)
from services.signals.proto_strategy import EmaCrossPolicy, EmaCrossStrategy

T0 = int(datetime(2026, 1, 1, tzinfo=UTC).timestamp() * 1000)
MIN = 60_000
HOUR = 60 * MIN
FREE = CostModel(fee_rate=0.0, slippage_bps=0.0)
# Quote = BRL and a large agent so risk sizing (not the equity cap) decides the size.
BIG = SizingModel(fx_brl_per_quote=1.0, agent_capital_brl=10_000.0, lot_size=0.001)


def bar(
    i: int,
    o: float,
    h: float | None = None,
    lo: float | None = None,
    c: float | None = None,
    v: float = 1.0,
    step: int = HOUR,
) -> Bar:
    c = o if c is None else c
    return Bar(
        T0 + i * step, o, max(o, c) if h is None else h, min(o, c) if lo is None else lo, c, v
    )


def flat(n: int, price: float = 100.0, step: int = HOUR) -> list[Bar]:
    return [bar(i, price, step=step) for i in range(n)]


def from_closes(closes: list[float], step: int = HOUR, volume: float = 1.0) -> list[Bar]:
    out, prev = [], closes[0]
    for i, c in enumerate(closes):
        out.append(Bar(T0 + i * step, prev, max(prev, c), min(prev, c), c, volume))
        prev = c
    return out


def walk(n: int, seed: int = 1, step: int = HOUR, vol: float = 0.01) -> list[Bar]:
    rng = random.Random(seed)
    closes, price = [], 100.0
    for _ in range(n):
        price *= math.exp(rng.gauss(0.0002, vol))
        closes.append(price)
    return from_closes(closes, step)


def plan_with(n: int, entries=(), exits=(), stop=0.5, target=10.0, max_bars=None) -> Plan:
    p = Plan.empty(n, stop=stop, target=target)
    for i in entries:
        p.entry[i] = 1.0
    for i in exits:
        p.exit[i] = True
    p.max_bars = max_bars
    return p


# --- backtester ----------------------------------------------------------------------------


def test_fills_at_the_next_open_with_fees_and_slippage() -> None:
    bars = [bar(0, 100), bar(1, 100, c=105), bar(2, 110), bar(3, 110)]
    result = run(bars, plan_with(4, entries=[0], exits=[1]), costs=CostModel(), sizing=BIG)
    (trade,) = result.trades
    # Risk budget 10000 x 0.25% / 2% stop = 1250 quote at the decision close (100) -> 12.5.
    assert trade.quantity == pytest.approx(12.5)
    assert trade.entry_index == 1 and trade.exit_index == 2  # decided at close 0, filled at 1
    assert trade.entry_price == pytest.approx(100 * 1.0002)
    assert trade.exit_price == pytest.approx(110 * 0.9998)
    entry_fee = 12.5 * 100.02 * 0.001
    exit_fee = 12.5 * 109.978 * 0.001
    assert trade.fees == pytest.approx(entry_fee + exit_fee)
    assert trade.pnl == pytest.approx(12.5 * (109.978 - 100.02) - entry_fee - exit_fee)
    assert result.end_equity == pytest.approx(10_000 + trade.pnl)
    assert result.fees == pytest.approx(entry_fee + exit_fee)
    assert result.slippage_cost == pytest.approx(12.5 * (100 + 110) * 0.0002)


def test_a_decision_never_fills_on_its_own_bar() -> None:
    bars = [bar(0, 100), bar(1, 120), bar(2, 120)]
    result = run(bars, plan_with(3, entries=[0]), costs=FREE, sizing=BIG)
    assert result.trades[0].entry_price == 120  # bar 1's open, not bar 0's close
    assert result.equity[0] == 10_000


def test_stop_inside_the_entry_bar_and_gap_through_the_stop() -> None:
    bars = [bar(0, 100), bar(1, 100, h=101, lo=97, c=99), bar(2, 99)]
    (trade,) = run(bars, plan_with(3, entries=[0], stop=0.02), costs=FREE, sizing=BIG).trades
    assert trade.reason == "stop" and trade.exit_index == 1 and trade.exit_price == 98

    gapped = [bar(0, 100), bar(1, 100, h=100, lo=99, c=99), bar(2, 95, h=96, lo=94, c=95)]
    (trade,) = run(gapped, plan_with(3, entries=[0], stop=0.02), costs=FREE, sizing=BIG).trades
    assert trade.reason == "stop" and trade.exit_price == 95  # filled at the gap open


def test_target_and_the_stop_wins_when_both_are_in_one_bar() -> None:
    bars = [bar(0, 100), bar(1, 100, h=105, lo=99.5, c=104), bar(2, 104)]
    (trade,) = run(
        bars, plan_with(3, entries=[0], stop=0.02, target=0.04), costs=FREE, sizing=BIG
    ).trades
    assert trade.reason == "target" and trade.exit_price == 104

    both = [bar(0, 100), bar(1, 100, h=105, lo=97, c=100), bar(2, 100)]
    (trade,) = run(
        both, plan_with(3, entries=[0], stop=0.02, target=0.04), costs=FREE, sizing=BIG
    ).trades
    assert trade.reason == "stop" and trade.exit_price == 98


def test_time_stop_and_flat_at_the_window_end() -> None:
    bars = flat(6)
    (trade,) = run(bars, plan_with(6, entries=[0], max_bars=2), costs=FREE, sizing=BIG).trades
    assert trade.reason == "time" and trade.entry_index == 1 and trade.exit_index == 3

    result = run(bars, plan_with(6, entries=[1]), start=0, end=4, costs=FREE, sizing=BIG)
    (trade,) = result.trades
    assert trade.reason == "window_end" and trade.exit_index == 3
    assert result.bars_in_market == 2 and len(result.equity) == 4


def test_window_ignores_decisions_outside_it() -> None:
    bars = flat(10)
    result = run(bars, plan_with(10, entries=[1, 5]), start=4, end=10, costs=FREE, sizing=BIG)
    assert [t.entry_index for t in result.trades] == [6]


def test_spot_never_shorts_an_exit_without_a_position() -> None:
    result = run(flat(5), plan_with(5, exits=[0, 1, 2]), costs=FREE, sizing=BIG)
    assert result.trades == [] and result.end_equity == 10_000


def test_sizing_matches_production_rules() -> None:
    btc = SizingModel.for_symbol("BTCUSDT")  # R$200 agent, 5.4 BRL/USDT, lot 0.00001
    equity = 200 / 5.4
    # Risk qty 10000 x 0.25% / 5.4 / (60000 x 2%) = 0.003858 > the 1.0x equity cap 0.000617.
    assert btc.quantity(60_000, 1.0, 0.05, equity, equity) == pytest.approx(0.00061)
    # A weak signal: risk sizing binds (strength 0.1 -> 0.0003858).
    assert btc.quantity(60_000, 0.1, 0.05, equity, equity) == pytest.approx(0.00038)
    # Survival: at R$150 the size shrinks by (150/200)^2.
    eq150 = 150 / 5.4
    assert btc.quantity(60_000, 0.1, 0.05, eq150, eq150) == pytest.approx(0.00021)
    # Below the 5 USDT minimum notional the agent cannot trade.
    tiny = 0.5 / 5.4 * 50
    assert btc.quantity(60_000, 1.0, 0.05, tiny, tiny) == 0.0
    # sizing_stop=None sizes on the strategy's own stop instead of the pipeline's 2%.
    own = SizingModel(
        fx_brl_per_quote=1.0, agent_capital_brl=10_000, lot_size=0.001, sizing_stop=None
    )
    assert own.quantity(100, 1.0, 0.05, 10_000, 10_000) == pytest.approx(5.0)


def test_entries_must_clear_the_fusion_bar_which_rises_with_losses() -> None:
    agent = SizingModel(fx_brl_per_quote=1.0, agent_capital_brl=200.0)
    assert agent.admits(0.6, 200.0) and not agent.admits(0.59, 200.0)
    assert not agent.admits(0.7, 100.0) and agent.admits(0.75, 100.0)  # 0.6 + 0.3 x 0.5
    assert agent.admits(0.6, 400.0)  # gains never lower the bar
    bars = flat(4)
    weak = plan_with(4, entries=[0])
    weak.entry[0] = 0.5
    assert run(bars, weak, costs=FREE, sizing=BIG).trades == []


def test_an_eliminated_agent_stops_entering() -> None:
    small = SizingModel(
        fx_brl_per_quote=1.0, agent_capital_brl=200.0, lot_size=0.001, min_notional=1.0
    )
    bars = [bar(0, 100), bar(1, 100, lo=10, c=10), bar(2, 10), bar(3, 10), bar(4, 10)]
    result = run(bars, plan_with(5, entries=[0, 2], stop=0.9), costs=FREE, sizing=small)
    assert result.eliminated and len(result.trades) == 1


def test_metrics() -> None:
    day = 86_400_000
    result = Result(
        start_equity=100.0, equity=[100, 110, 99, 121], times=[T0 + k * day for k in range(4)]
    )
    result.trades = [Trade(0, 1, 1, 1, 1, 0.5, p, "signal") for p in (10.0, -5.0, 20.0)]
    result.fees, result.slippage_cost, result.bars_in_market = 1.0, 0.5, 2
    m = metrics(result)
    assert m["total_return"] == pytest.approx(0.21)
    assert m["max_drawdown"] == pytest.approx(0.1)
    assert m["win_rate"] == pytest.approx(2 / 3)
    assert m["profit_factor"] == pytest.approx(6.0)
    assert m["expectancy"] == pytest.approx(25 / 3 / 100)
    assert m["exposure"] == pytest.approx(0.5)
    assert m["fee_drag"] == pytest.approx(0.015)
    assert m["trades"] == 3 and m["sharpe"] > 0 and m["sortino"] > 0


def test_buy_and_hold_pays_two_fees() -> None:
    bars = [bar(0, 100), bar(1, 100), bar(2, 120)]
    result = buy_and_hold(bars, costs=CostModel(fee_rate=0.001, slippage_bps=0), sizing=BIG)
    q = math.floor(10_000 / (100 * 1.001) / 0.001) * 0.001
    expected = 10_000 - q * 100 * 1.001 + q * 120 * 0.999
    assert result.end_equity == pytest.approx(expected)


# --- walk-forward ---------------------------------------------------------------------------


def test_windows_roll_without_overlap_or_gaps() -> None:
    times = [T0 + k * HOUR for k in range(24 * 40)]
    wins = windows(times, train_days=10, test_days=5)
    assert len(wins) == 6
    for w in wins:
        assert w.train_start < w.train_end == w.test_start < w.test_end
        assert times[w.train_end] - times[w.train_start] == 10 * 24 * HOUR
    for a, b in zip(wins, wins[1:], strict=False):
        assert a.test_end == b.test_start  # tests are contiguous and never overlap


def test_walk_forward_never_reads_the_future() -> None:
    bars = walk(24 * 40, seed=3)
    configs = [EmaCross(fast=3, slow=8, cooldown_bars=1), TsMomentum(lookback=12, vol_period=24)]
    wins = windows([b.open_time for b in bars], train_days=10, test_days=5)
    base = walk_forward(bars, configs, wins, costs=CostModel(), sizing=BIG, min_trades=1)
    cut = wins[1].test_end
    future = bars[:cut] + [
        Bar(b.open_time, b.open * 3, b.high * 3, b.low * 3, b.close * 3, 9) for b in bars[cut:]
    ]
    other = walk_forward(future, configs, wins, costs=CostModel(), sizing=BIG, min_trades=1)
    assert other.chosen[:2] == base.chosen[:2]
    assert other.per_window[:2] == base.per_window[:2]
    n = cut - wins[0].test_start
    assert other.oos.equity[:n] == base.oos.equity[:n]


def test_walk_forward_abstains_when_nothing_made_money_on_train() -> None:
    bars = from_closes([100 * (0.999**k) for k in range(24 * 30)])  # steady decline
    configs = [TsMomentum(lookback=3, vol_period=5)]
    wins = windows([b.open_time for b in bars], train_days=10, test_days=5)
    wf = walk_forward(bars, configs, wins, costs=CostModel(), sizing=BIG, min_trades=0)
    assert all(c is None for c in wf.chosen)
    assert set(wf.oos.equity) == {wf.oos.start_equity}


def test_deflated_sharpe_penalizes_more_trials() -> None:
    rng = random.Random(7)
    rets = [rng.gauss(0.002, 0.01) for _ in range(250)]
    assert expected_max_sharpe(1, 0.01) == 0.0
    assert expected_max_sharpe(1000, 0.01) > expected_max_sharpe(10, 0.01) > 0
    few, many = deflated_sharpe(rets, 2, 0.01), deflated_sharpe(rets, 1000, 0.01)
    assert 0 <= many < few <= 1


# --- indicators -----------------------------------------------------------------------------


def test_indicators_are_causal_and_correct() -> None:
    assert ind.sma([1, 2, 3, 4], 2)[1:] == [1.5, 2.5, 3.5]
    assert ind.prior_max([1, 5, 2, 3, 1], 2)[2:] == [5, 5, 3]  # excludes the current value
    assert ind.prior_min([5, 1, 2, 3, 4], 2)[2:] == [1, 1, 2]
    r = ind.rsi([float(x) for x in range(30)], 2)
    assert r[-1] == 100.0 and math.isnan(r[1])
    assert ind.percentile_rank([1, 2, 3, 4], 4)[3] == 1.0
    assert ind.rolling_std([2, 4, 4, 4, 5, 5, 7, 9], 8)[-1] == pytest.approx(2.0)


def test_higher_timeframe_ema_waits_for_the_hour_to_close() -> None:
    bars = from_closes([float(100 + k) for k in range(16)], step=15 * MIN)
    htf = ind.higher_timeframe_ema(bars, 60, 1)
    assert all(math.isnan(v) for v in htf[:3])
    assert htf[3] == 103 and htf[4:7] == [103, 103, 103] and htf[7] == 107
    changed = bars[:5] + [Bar(b.open_time, 1, 1, 1, 1, 1) for b in bars[5:]]
    assert ind.higher_timeframe_ema(changed, 60, 1)[:5] == htf[:5]


# --- strategies ------------------------------------------------------------------------------


def test_ema_cross_matches_the_production_strategy() -> None:
    closes = [100 + k * 0.5 for k in range(20)]
    closes += [closes[-1] - k * 0.5 for k in range(1, 21)]
    closes += [closes[-1] + k * 0.5 for k in range(1, 21)]
    live = EmaCrossStrategy(EmaCrossPolicy())
    produced = []
    start = datetime(2026, 1, 1, tzinfo=UTC)
    for k, c in enumerate([*closes, closes[-1]]):
        signal = live.observe("BINANCE_SPOT:BTCUSDT", c, start + timedelta(minutes=k, seconds=30))
        if signal is not None:
            index = int((signal.observed_at - start).total_seconds() // 60) - 1
            produced.append((index, signal.direction))
    plan = EmaCross().plan(from_closes(closes, MIN))
    lab = [(i, SignalDirection.LONG) for i, s in enumerate(plan.entry) if s] + [
        (i, SignalDirection.SHORT) for i, x in enumerate(plan.exit) if x
    ]
    assert produced and sorted(lab) == sorted(produced)


def test_ema_trend_needs_the_higher_timeframe_uptrend() -> None:
    wave = [100 + 3 * math.sin(k / 6) for k in range(400)]
    up = from_closes([w + 0.2 * k for k, w in enumerate(wave)])
    down = from_closes([w - 0.2 * k + 100 for k, w in enumerate(wave)])
    strategy = EmaTrend(fast=3, slow=8, htf_minutes=60, htf_period=50)
    trend = ind.higher_timeframe_ema(up, 60, 50)
    plan = strategy.plan(up)
    entries = [i for i, s in enumerate(plan.entry) if s]
    assert entries and all(up[i].close > trend[i] for i in entries)
    assert not any(strategy.plan(down).entry)


def test_donchian_needs_a_breakout_on_volume() -> None:
    bars = flat(30)
    loud = bars + [Bar(T0 + 30 * HOUR, 100, 106, 100, 105, 3.0)]
    quiet = bars + [Bar(T0 + 30 * HOUR, 100, 106, 100, 105, 1.0)]
    strategy = Donchian(entry_period=20, exit_period=10, volume_mult=1.5)
    assert strategy.plan(loud).entry[30] == 1.0
    assert strategy.plan(quiet).entry[30] == 0.0
    fall = bars + [Bar(T0 + 30 * HOUR, 100, 100, 95, 95, 1.0)]
    assert strategy.plan(fall).exit[30]


def test_connors_buys_dips_only_in_an_uptrend() -> None:
    rise = [100 + 0.3 * k for k in range(260)]
    dip = rise + [rise[-1] - 2, rise[-1] - 4, rise[-1] - 6, rise[-1] + 2]
    strategy = ConnorsRsi(trend_period=200)
    plan = strategy.plan(from_closes(dip))
    assert any(plan.entry[260:263]) and plan.exit[263] and plan.max_bars == 10
    fallen = [200 - 0.3 * k for k in range(260)]
    down = fallen + [fallen[-1] - 2, fallen[-1] - 4, fallen[-1] - 6]
    assert not any(strategy.plan(from_closes(down)).entry)


def test_time_series_momentum_targets_volatility() -> None:
    calm = from_closes([100 * 1.001**k * (1 + 0.001 * (-1) ** k) for k in range(200)])
    wild = from_closes([100 * 1.001**k * (1 + 0.02 * (-1) ** k) for k in range(200)])
    strategy = TsMomentum(lookback=24, vol_period=48, target_vol=0.5)
    s_calm, s_wild = strategy.plan(calm).entry[-1], strategy.plan(wild).entry[-1]
    assert s_calm == 1.0 and 0 < s_wild < s_calm
    falling = from_closes([100 * 0.999**k for k in range(200)])
    plan = strategy.plan(falling)
    assert not any(plan.entry) and plan.exit[-1]


def test_regime_filter_only_removes_entries() -> None:
    bars = walk(1500, seed=5)
    inner = TsMomentum(lookback=12, vol_period=24)
    base, filtered = inner.plan(bars), RegimeFilter(inner, window=200, low=0.9, high=1.0).plan(bars)
    assert sum(1 for s in filtered.entry if s) < sum(1 for s in base.entry if s)
    assert all(f <= b for f, b in zip(filtered.entry, base.entry, strict=True))
    assert filtered.exit == base.exit and filtered.stop == base.stop


def test_every_strategy_always_has_a_stop_and_a_target_and_round_trips() -> None:
    bars = walk(600, seed=9)
    strategies = [
        EmaCross(),
        EmaTrend(),
        Donchian(),
        ConnorsRsi(),
        TsMomentum(),
        RegimeFilter(Donchian()),
        RandomEntry(seed=4),
    ]
    for strategy in strategies:
        plan = strategy.plan(bars)
        assert len(plan.entry) == len(plan.exit) == len(bars)
        assert all(0 < s < 1 for s in plan.stop) and all(t > 0 for t in plan.target)
        assert all(0 <= s <= 1 for s in plan.entry)
        again = build(json.loads(json.dumps(strategy.describe())))
        assert again == strategy
    assert RandomEntry(seed=4).plan(bars).entry == RandomEntry(seed=4).plan(bars).entry
    with pytest.raises(ValueError):
        build({"strategy": "nope"})


# --- data -----------------------------------------------------------------------------------


def _kline(t: int) -> list[object]:
    return [t, "1", "2", "0.5", "1.5", "10", t + MIN - 1, "0", 1, "0", "0", "0"]


def test_download_pages_forward_and_deduplicates() -> None:
    start = datetime(2026, 1, 1, tzinfo=UTC)
    calls = []

    def fake(params: dict[str, object]) -> list[list[object]]:
        calls.append(params)
        t = int(params["startTime"])  # type: ignore[arg-type]
        stop = int(params["endTime"])  # type: ignore[arg-type]
        return [_kline(x) for x in range(t, min(t + 1000 * MIN, stop + 1), MIN)]

    bars = download("BTCUSDT", "1m", start, start + timedelta(minutes=2500), fetch=fake)
    assert len(bars) == 2500 and len(calls) == 3
    assert [b.open_time for b in bars] == sorted({b.open_time for b in bars})
    assert parse_klines([_kline(T0)])[0] == Bar(T0, 1.0, 2.0, 0.5, 1.5, 10.0)


def test_cache_round_trip_resample_and_gaps(tmp_path) -> None:
    bars = from_closes([float(k) + 1 for k in range(10)], MIN)
    path = tmp_path / "X-1m.csv.gz"
    save(bars, path)
    assert load(path) == bars
    five = resample(bars, 5)
    assert len(five) == 2
    assert five[0] == Bar(
        T0,
        bars[0].open,
        max(b.high for b in bars[:5]),
        min(b.low for b in bars[:5]),
        bars[4].close,
        5.0,
    )
    assert check_gaps(bars, "1m") == 0 and check_gaps(bars[:3] + bars[5:], "1m") == 2


# --- lab CLI ------------------------------------------------------------------------------


def test_grids_and_labels() -> None:
    assert len(grid("ema_cross", 1)) == 8
    assert grid("ema_cross", 1)[0] == EmaCross(fast=5, slow=20, cooldown_bars=15)
    assert {len(grid(f, 60)) for f in ("donchian", "connors_rsi")} == {16}
    assert all(isinstance(c, RegimeFilter) for c in grid("regime", 60))
    assert label(RegimeFilter(Donchian(), low=0.5, high=1.0).describe()).startswith("regime[0.5-1]")


def test_backtest_command_reads_the_cache(tmp_path, monkeypatch, capsys) -> None:
    monkeypatch.setenv("PROTO_RESEARCH_DATA", str(tmp_path))
    save(walk(500, seed=2), tmp_path / "klines" / "BTCUSDT-1h.csv.gz")
    from services.research import lab

    lab.bars_of.cache_clear()
    spec = json.dumps({"strategy": "ts_momentum", "lookback": 12, "vol_period": 24})
    assert main(["backtest", "--symbol", "BTCUSDT", "--timeframe", "1h", "--spec", spec]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["spec"]["lookback"] == 12 and "max_drawdown" in out
    lab.bars_of.cache_clear()


def test_render_marks_robust_rows_only_when_every_bar_is_cleared() -> None:
    oos = {
        "total_return": 0.2,
        "max_drawdown": 0.05,
        "sharpe": 1.5,
        "trades": 20,
        "win_rate": 0.5,
        "profit_factor": 1.5,
        "expectancy": 0.01,
        "exposure": 0.2,
        "fee_drag": 0.02,
    }
    weak = dict(oos, max_drawdown=0.3)
    row = {
        "symbol": "BTCUSDT",
        "timeframe": "1h",
        "family": "donchian",
        "configs": 16,
        "per_window": [{"return": 0.05, "trades": 3}] * 4,
        "dsr": 0.4,
        "fixed": [],
    }
    bench = {
        "symbol": "BTCUSDT",
        "timeframe": "1h",
        "buy_and_hold": dict(oos, total_return=0.1, max_drawdown=0.3, sharpe=0.5),
        "random": dict(oos, total_return=-0.2),
    }
    text = render(
        {"results": [dict(row, oos=oos), dict(row, family="ts", oos=weak)], "benchmarks": [bench]}
    )
    lines = [
        x for x in text.splitlines() if x.startswith("| 1h | donchian") or x.startswith("| 1h | ts")
    ]
    assert lines[0].endswith("| yes |") and lines[1].endswith("| no |")
