"""Strategy lab CLI: ``python -m services.research.lab download|backtest|walkforward|report``.

Research only: public market data, virtual R$200 agents, no credentials and no order authority.
Results land in the gitignored data directory; ``report`` renders the tables as Markdown.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from collections.abc import Iterable, Sequence
from dataclasses import asdict
from datetime import UTC, datetime
from functools import lru_cache
from itertools import product
from pathlib import Path
from statistics import fmean
from typing import Any

from services.research import data as market
from services.research.backtest import CostModel, SizingModel, metrics, run
from services.research.strategies import (
    ConnorsRsi,
    Donchian,
    EmaCross,
    EmaTrend,
    RandomEntry,
    RegimeFilter,
    TsMomentum,
    build,
)
from services.research.walkforward import (
    benchmark_oos,
    deflated_sharpe,
    fixed_oos,
    per_period_sharpe,
    walk_forward,
    windows,
)

TIMEFRAMES: dict[str, int] = {"1m": 1, "5m": 5, "15m": 15, "1h": 60, "4h": 240}
FAMILIES_BY_TIMEFRAME: dict[str, tuple[str, ...]] = {
    "1m": ("ema_cross",),
    "5m": ("ema_cross", "ema_trend", "donchian", "connors_rsi"),
    "15m": ("ema_cross", "ema_trend", "donchian", "connors_rsi", "ts_momentum", "regime"),
    "1h": ("ema_cross", "ema_trend", "donchian", "connors_rsi", "ts_momentum", "regime"),
    "4h": ("ema_cross", "ema_trend", "donchian", "connors_rsi", "ts_momentum", "regime"),
}
TRAIN_DAYS, TEST_DAYS = 120, 30
RANDOM_SEEDS = 20


def _bars_for(hours: float, minutes: int) -> int:
    return max(1, round(hours * 60 / minutes))


def grid(family: str, minutes: int) -> list[Any]:
    """The configurations tried for one family on one timeframe (all count as trials)."""

    from services.agents.roster import EMA_PARAM_LADDER

    per_year = 365 * 24 * 60 / minutes
    if family == "ema_cross":
        cooldown = max(1, math.ceil(15 / minutes))
        return [EmaCross(fast=f, slow=s, cooldown_bars=cooldown) for f, s in EMA_PARAM_LADDER]
    if family == "ema_trend":
        htfs = [h for h in ((60, 200), (240, 50), (1440, 20)) if h[0] >= minutes][:2]
        return [
            EmaTrend(fast=f, slow=s, htf_minutes=hm, htf_period=hp, stop_atr=st, target_atr=tg)
            for (f, s), (st, tg), (hm, hp) in product(
                ((12, 26), (20, 50)), ((2.0, 4.0), (2.0, 8.0), (3.0, 6.0)), htfs
            )
        ]
    if family == "donchian":
        return [
            Donchian(entry_period=e, exit_period=x, volume_mult=v, target_atr=t)
            for e, x, v, t in product((20, 55), (10, 20), (1.0, 1.5), (4.0, 10.0))
        ]
    if family == "connors_rsi":
        return [
            ConnorsRsi(rsi_entry=r, max_hold=h, stop_atr=s, target_atr=t)
            for r, h, s, t in product((5.0, 10.0), (5, 10), (2.0, 3.0), (2.0, 4.0))
        ]
    if family == "ts_momentum":
        return [
            TsMomentum(
                lookback=_bars_for(lb, minutes),
                confirm_lookback=_bars_for(c, minutes) if c else 0,
                vol_period=_bars_for(72, minutes),
                bars_per_year=per_year,
                target_atr=t,
            )
            for lb, c, t in product((24, 72, 168), (0, 720), (6.0, 12.0))
        ]
    if family == "regime":
        inner: list[Any] = [
            TsMomentum(
                lookback=_bars_for(lb, minutes),
                confirm_lookback=_bars_for(c, minutes) if c else 0,
                vol_period=_bars_for(72, minutes),
                bars_per_year=per_year,
                target_atr=12.0,
            )
            for lb, c in product((72, 168), (0, 720))
        ] + [Donchian(entry_period=e, volume_mult=1.0, target_atr=10.0) for e in (20, 55)]
        window = _bars_for(24 * 30, minutes)
        return [
            RegimeFilter(i, window=window, low=lo, high=hi)
            for i, (lo, hi) in product(inner, ((0.0, 0.5), (0.5, 1.0)))
        ]
    raise ValueError(family)


@lru_cache(maxsize=4)
def bars_of(symbol: str, timeframe: str) -> tuple[market.Bar, ...]:
    if timeframe == "4h":
        return tuple(market.resample(market.load_cached(symbol, "1h"), 240))
    return tuple(market.load_cached(symbol, timeframe))


def _m(result: Any, minutes: int) -> dict[str, float]:
    return {
        k: (v if math.isfinite(v) else 99.0)
        for k, v in metrics(result, bar_minutes=minutes).items()
    }


def research_task(task: tuple[str, str, str]) -> dict[str, Any]:
    symbol, timeframe, family = task
    minutes = TIMEFRAMES[timeframe]
    bars = bars_of(symbol, timeframe)
    costs, sizing = CostModel(), SizingModel.for_symbol(symbol)
    wins = windows([b.open_time for b in bars], train_days=TRAIN_DAYS, test_days=TEST_DAYS)
    configs = grid(family, minutes)
    wf = walk_forward(bars, configs, wins, costs=costs, sizing=sizing)
    from services.research.backtest import daily_returns

    return {
        "symbol": symbol,
        "timeframe": timeframe,
        "family": family,
        "configs": len(configs),
        "windows": [(bars[w.test_start].open_time, bars[w.test_end - 1].open_time) for w in wins],
        "oos": _m(wf.oos, minutes),
        "oos_daily": daily_returns(wf.oos.equity, wf.oos.times, wf.oos.start_equity),
        "per_window": wf.per_window,
        "chosen": wf.chosen,
        "fixed": [
            {"spec": c.describe(), "oos": _m(r, minutes), "sr": per_period_sharpe(r)}
            for c, r in zip(configs, wf.fixed_oos, strict=True)
        ],
    }


def benchmark_task(task: tuple[str, str]) -> dict[str, Any]:
    symbol, timeframe = task
    minutes = TIMEFRAMES[timeframe]
    bars = bars_of(symbol, timeframe)
    costs, sizing = CostModel(), SizingModel.for_symbol(symbol)
    wins = windows([b.open_time for b in bars], train_days=TRAIN_DAYS, test_days=TEST_DAYS)
    hold = _bars_for(12, minutes)
    randoms = [
        _m(
            fixed_oos(
                bars,
                RandomEntry(probability=1 / (3 * hold), hold=hold, seed=s).plan(bars),
                wins,
                costs=costs,
                sizing=sizing,
            ),
            minutes,
        )
        for s in range(RANDOM_SEEDS)
    ]
    return {
        "symbol": symbol,
        "timeframe": timeframe,
        "buy_and_hold": _m(benchmark_oos(bars, wins, costs=costs, sizing=sizing), minutes),
        "random": {k: fmean(r[k] for r in randoms) for k in randoms[0]},
        "random_positive_share": sum(r["total_return"] > 0 for r in randoms) / len(randoms),
    }


STRESS_COSTS = CostModel(fee_rate=0.001, slippage_bps=10.0)


def survivor_task(task: tuple[str, str, tuple[str, ...]]) -> dict[str, Any]:
    """Re-check a fixed configuration that was positive on every symbol's test windows."""

    timeframe, spec_json, symbols = task
    minutes = TIMEFRAMES[timeframe]
    strategy = build(json.loads(spec_json))
    out: dict[str, Any] = {"timeframe": timeframe, "spec": json.loads(spec_json), "by_symbol": {}}
    for symbol in symbols:
        bars = bars_of(symbol, timeframe)
        sizing = SizingModel.for_symbol(symbol)
        wins = windows([b.open_time for b in bars], train_days=TRAIN_DAYS, test_days=TEST_DAYS)
        plan = strategy.plan(bars)
        base = fixed_oos(bars, plan, wins, costs=CostModel(), sizing=sizing)
        stress = fixed_oos(bars, plan, wins, costs=STRESS_COSTS, sizing=sizing)
        full = run(bars, plan, sizing=sizing)
        from services.research.backtest import daily_returns

        out["by_symbol"][symbol] = {
            "oos": _m(base, minutes),
            "oos_daily": daily_returns(base.equity, base.times, base.start_equity),
            "oos_stress": _m(stress, minutes),
            "full_period": _m(full, minutes),
        }
    return out


def research(
    symbols: Sequence[str], timeframes: Sequence[str], out: Path, processes: int = 4
) -> dict[str, Any]:
    from multiprocessing import Pool

    tasks = [(s, tf, f) for s in symbols for tf in timeframes for f in FAMILIES_BY_TIMEFRAME[tf]]
    tasks.sort(key=lambda t: TIMEFRAMES[t[1]])  # slow 1m last but grouped
    bench = [(s, tf) for s in symbols for tf in timeframes]
    with Pool(processes) as pool:
        results = pool.map(research_task, tasks, chunksize=1)
        benchmarks = pool.map(benchmark_task, bench, chunksize=1)
        by_config: dict[tuple[str, str], dict[str, float]] = {}
        for r in results:
            for f in r["fixed"]:
                key = (r["timeframe"], json.dumps(f["spec"], sort_keys=True))
                by_config.setdefault(key, {})[r["symbol"]] = f["oos"]["total_return"]
        candidates = [
            (tf, spec, tuple(symbols))
            for (tf, spec), rets in sorted(by_config.items())
            if len(rets) == len(symbols) and min(rets.values()) > 0
        ]
        survivors = pool.map(survivor_task, candidates, chunksize=1)
    # Deflate every walk-forward result by everything tried on that symbol.
    for symbol in symbols:
        mine = [r for r in results if r["symbol"] == symbol]
        trials = sum(r["configs"] for r in mine)
        srs = [f["sr"] for r in mine for f in r["fixed"]]
        mean = fmean(srs) if srs else 0.0
        var = fmean((x - mean) ** 2 for x in srs) if len(srs) > 1 else 0.0
        for r in mine:
            r["trials"] = trials
            r["sharpe_variance"] = var
            r["dsr"] = deflated_sharpe(r["oos_daily"], trials, var)
        for sv in survivors:
            row = sv["by_symbol"][symbol]
            row["dsr"] = deflated_sharpe(row.pop("oos_daily"), trials, var)
            row["trials"] = trials
    payload = {
        "generated_at": datetime.now(UTC).isoformat(),
        "method": {
            "train_days": TRAIN_DAYS,
            "test_days": TEST_DAYS,
            "costs": asdict(CostModel()),
            "sizing": asdict(SizingModel()),
            "random_seeds": RANDOM_SEEDS,
        },
        "results": results,
        "benchmarks": benchmarks,
        "survivors": survivors,
    }
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, indent=1))
    return payload


# --- report ---------------------------------------------------------------------------------


def _pct(x: float) -> str:
    return f"{x * 100:+.1f}%"


def robust(r: dict[str, Any], bench: dict[str, Any]) -> bool:
    """Our bar for 'beats costs robustly' (see the report's method section)."""

    o = r["oos"]
    traded = [w for w in r["per_window"] if w["trades"] > 0]
    positive_windows = sum(w["return"] > 0 for w in traded)
    return (
        o["total_return"] > 0
        and o["sharpe"] > bench["buy_and_hold"]["sharpe"]
        and o["total_return"] > bench["random"]["total_return"]
        and o["profit_factor"] > 1.1
        and o["trades"] >= 10
        and o["max_drawdown"] < 0.15
        and len(traded) >= 3
        and positive_windows >= len(traded) / 2
    )


def label(spec: dict[str, Any]) -> str:
    """Short human label of a strategy spec."""

    keys = {
        "ema_cross": ("fast", "slow"),
        "ema_trend": ("fast", "slow", "htf_minutes", "htf_period", "stop_atr", "target_atr"),
        "donchian": ("entry_period", "exit_period", "volume_mult", "target_atr"),
        "connors_rsi": ("rsi_entry", "max_hold", "stop_atr", "target_atr"),
        "ts_momentum": ("lookback", "confirm_lookback", "target_atr"),
    }
    kind = spec["strategy"]
    if kind == "regime":
        return f"regime[{spec['low']:g}-{spec['high']:g}]({label(spec['inner'])})"
    args = ",".join(f"{spec[k]:g}" for k in keys.get(kind, ()))
    return f"{kind}({args})"


def render(payload: dict[str, Any]) -> str:
    results, benchmarks = payload["results"], payload["benchmarks"]
    bench = {(b["symbol"], b["timeframe"]): b for b in benchmarks}
    lines: list[str] = []
    for symbol in sorted({r["symbol"] for r in results}):
        lines += [
            f"### {symbol}",
            "",
            "| TF | Strategy | OOS return | Max DD | Sharpe | Trades | Win | PF | "
            "Exp./trade | Exposure | Fee drag | +/traded/all windows | DSR | Robust |",
            "|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|",
        ]
        mine = [r for r in results if r["symbol"] == symbol]
        for tf in TIMEFRAMES:
            b = bench.get((symbol, tf))
            if b is None:
                continue
            for r in [x for x in mine if x["timeframe"] == tf]:
                o = r["oos"]
                traded = sum(w["trades"] > 0 for w in r["per_window"])
                pos = sum(w["return"] > 0 for w in r["per_window"] if w["trades"] > 0)
                lines.append(
                    f"| {tf} | {r['family']} (wf, {r['configs']} cfg) | "
                    f"{_pct(o['total_return'])} | "
                    f"{o['max_drawdown'] * 100:.1f}% | {o['sharpe']:.2f} | {int(o['trades'])} | "
                    f"{o['win_rate'] * 100:.0f}% | {min(o['profit_factor'], 99):.2f} | "
                    f"{_pct(o['expectancy'])} | {o['exposure'] * 100:.0f}% | "
                    f"{o['fee_drag'] * 100:.1f}% | {pos}/{traded}/{len(r['per_window'])} | "
                    f"{r['dsr']:.2f} | {'yes' if robust(r, b) else 'no'} |"
                )
            bh, rnd = b["buy_and_hold"], b["random"]
            lines.append(
                f"| {tf} | buy & hold | {_pct(bh['total_return'])} | "
                f"{bh['max_drawdown'] * 100:.1f}% "
                f"| {bh['sharpe']:.2f} | 1 | | | | 100% | {bh['fee_drag'] * 100:.1f}% | | | |"
            )
            lines.append(
                f"| {tf} | random entry (mean of {RANDOM_SEEDS}) | {_pct(rnd['total_return'])} | "
                f"{rnd['max_drawdown'] * 100:.1f}% | {rnd['sharpe']:.2f} | {rnd['trades']:.0f} | "
                f"{rnd['win_rate'] * 100:.0f}% | {rnd['profit_factor']:.2f} | "
                f"{_pct(rnd['expectancy'])} | {rnd['exposure'] * 100:.0f}% | "
                f"{rnd['fee_drag'] * 100:.1f}% | | | |"
            )
        lines.append("")
    lines += [
        "### Fixed configurations positive on every symbol (test windows, no per-window selection)",
        "",
        "Chosen *after* seeing the test results, so they are deflated by every configuration "
        "tried on the symbol (DSR) and re-run with 10 bps slippage and over the full year.",
        "",
        "| TF | Config | Symbol | OOS return | Max DD | Trades | PF | OOS @10bps | "
        "Full year | Full-year DD | DSR |",
        "|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for sv in payload.get("survivors", []):
        for symbol, row in sorted(sv["by_symbol"].items()):
            o, st, fy = row["oos"], row["oos_stress"], row["full_period"]
            lines.append(
                f"| {sv['timeframe']} | {label(sv['spec'])} | {symbol} | "
                f"{_pct(o['total_return'])} | {o['max_drawdown'] * 100:.1f}% | "
                f"{int(o['trades'])} | {min(o['profit_factor'], 99):.2f} | "
                f"{_pct(st['total_return'])} | {_pct(fy['total_return'])} | "
                f"{fy['max_drawdown'] * 100:.1f}% | {row['dsr']:.2f} |"
            )
    lines.append("")
    # Production baseline: the current EMA 5/20 on 1m, fixed (no selection), test windows only.
    lines += [
        "### Current production rule (EMA 5/20, 1m, fixed params, test windows only)",
        "",
        "| Symbol | OOS return | Max DD | Trades | Win | PF | Fee drag |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    for r in results:
        if r["family"] == "ema_cross" and r["timeframe"] == "1m":
            f = r["fixed"][0]["oos"]
            lines.append(
                f"| {r['symbol']} | {_pct(f['total_return'])} | {f['max_drawdown'] * 100:.1f}% | "
                f"{int(f['trades'])} | {f['win_rate'] * 100:.0f}% | "
                f"{min(f['profit_factor'], 99):.2f} | {f['fee_drag'] * 100:.1f}% |"
            )
    lines.append("")
    return "\n".join(lines)


# --- CLI ------------------------------------------------------------------------------------


def _results_path() -> Path:
    return market.data_dir().parent / "results" / "walkforward.json"


def main(argv: Iterable[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m services.research.lab")
    sub = parser.add_subparsers(dest="command", required=True)
    d = sub.add_parser("download", help="fetch public klines into the gitignored cache")
    d.add_argument("--symbols", default=",".join(market.SYMBOLS))
    d.add_argument("--intervals", default="1m,5m,15m,1h")
    d.add_argument("--days", type=int, default=365)
    b = sub.add_parser("backtest", help="one strategy on one cached series, full period")
    b.add_argument("--symbol", default="BTCUSDT")
    b.add_argument("--timeframe", default="1h")
    b.add_argument("--spec", default='{"strategy": "ema_cross"}', help="JSON strategy spec")
    w = sub.add_parser("walkforward", help="walk-forward research over the cached data")
    w.add_argument("--symbols", default=",".join(market.SYMBOLS))
    w.add_argument("--timeframes", default=",".join(TIMEFRAMES))
    w.add_argument("--processes", type=int, default=4)
    r = sub.add_parser("report", help="render the last walk-forward results as Markdown")
    r.add_argument("--out", default="-")
    args = parser.parse_args(list(argv) if argv is not None else None)

    if args.command == "download":
        market.download_all(args.symbols.split(","), args.intervals.split(","), days=args.days)
    elif args.command == "backtest":
        bars = list(bars_of(args.symbol, args.timeframe))
        strategy = build(json.loads(args.spec))
        result = run(bars, strategy.plan(bars), sizing=SizingModel.for_symbol(args.symbol))
        print(json.dumps({"spec": strategy.describe(), **metrics(result)}, indent=1))
    elif args.command == "walkforward":
        payload = research(
            args.symbols.split(","), args.timeframes.split(","), _results_path(), args.processes
        )
        print(f"{len(payload['results'])} walk-forward runs -> {_results_path()}")
    else:
        text = render(json.loads(_results_path().read_text()))
        if args.out == "-":
            print(text)
        else:
            Path(args.out).write_text(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
