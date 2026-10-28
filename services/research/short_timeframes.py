"""Short-timeframe EMA study (1-10 minute bars, 3% stop), research only.

``python -m services.research.short_timeframes <days> <out.json>``: downloads Binance public 1m
klines (read-only, cached under ``$PROTO_RESEARCH_DATA``), resamples them to 1/2/3/5/10-minute
bars and backtests the production EMA cross with a 3% stop and 6% target, with and without a 1h
EMA200 trend filter. Each configuration is reported on the first half of the period (train, for
selection) and the second half (test). Costs: real Binance taker (0.1%/side + 2 bps) and
testnet-like (no fee + 2 bps). Report: docs/research/short-timeframes-2026-10-28.md.
"""

from __future__ import annotations

import json
import sys
from dataclasses import replace
from datetime import UTC, datetime, timedelta

from services.research import data as market
from services.research import indicators as ind
from services.research.backtest import CostModel, SizingModel, metrics, run
from services.research.strategies import EmaCross, Plan

DAYS = int(sys.argv[1]) if len(sys.argv) > 1 else 120
SYMBOLS = ("BTCUSDT", "ETHUSDT", "SOLUSDT")
TFS = (1, 2, 3, 5, 10)
REAL = CostModel(fee_rate=0.001, slippage_bps=2.0)
NO_FEE = CostModel(fee_rate=0.0, slippage_bps=2.0)


def bars_for(symbol: str) -> list[market.Bar]:
    path = market.cache_path(symbol, f"1m-{DAYS}d")
    if path.exists():
        return market.load(path)
    end = datetime.now(UTC).replace(second=0, microsecond=0)
    got = market.download(symbol, "1m", end - timedelta(days=DAYS), end)
    market.save(got, path)
    return got


def trend_filtered(plan: Plan, bars, htf_minutes: int, period: int) -> Plan:
    trend = ind.higher_timeframe_ema(bars, htf_minutes, period)
    entry = [
        e if (t == t and b.close > t) else 0.0  # NaN-safe
        for e, t, b in zip(plan.entry, trend, bars, strict=True)
    ]
    return replace(plan, entry=entry)


def variants():
    for fast, slow in ((5, 20), (9, 21), (12, 26), (20, 50)):
        for cooldown in (15, 60):
            base = EmaCross(
                fast=fast, slow=slow, cooldown_bars=cooldown, stop_frac=0.03, target_frac=0.06
            )
            yield f"ema{fast}/{slow} cd{cooldown}", base, None
            yield f"ema{fast}/{slow} cd{cooldown} +trend1hEMA200", base, (60, 200)


def evaluate(bars, plan, start, end, costs, symbol):
    sizing = SizingModel.for_symbol(symbol, sizing_stop=0.03)
    res = run(bars, plan, start=start, end=end, costs=costs, sizing=sizing)
    m = metrics(res)
    return {
        "ret": m.get("total_return", 0.0),
        "trades": len(res.trades),
        "win": m.get("win_rate", 0.0),
        "pf": m.get("profit_factor", 0.0),
        "dd": m.get("max_drawdown", 0.0),
        "fees": res.fees,
    }


def main() -> None:
    out = []
    for symbol in SYMBOLS:
        one = bars_for(symbol)
        print(symbol, len(one), "1m bars", flush=True)
        for tf in TFS:
            bars = one if tf == 1 else market.resample(one, tf)
            half = len(bars) // 2
            bh_test = bars[-1].close / bars[half].close - 1
            for name, strat, trend in variants():
                plan = strat.plan(bars)
                if trend:
                    plan = trend_filtered(plan, bars, *trend)
                for cname, costs in (("real", REAL), ("nofee", NO_FEE)):
                    tr = evaluate(bars, plan, 0, half, costs, symbol)
                    te = evaluate(bars, plan, half, len(bars), costs, symbol)
                    out.append(
                        {
                            "symbol": symbol,
                            "tf": tf,
                            "variant": name,
                            "costs": cname,
                            "train": tr,
                            "test": te,
                            "bh_test": bh_test,
                        }
                    )
            print(symbol, tf, "done", flush=True)
    json.dump(out, open(sys.argv[2], "w"))


if __name__ == "__main__":
    main()
