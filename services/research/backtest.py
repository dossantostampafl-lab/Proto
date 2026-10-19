"""Event-driven, long-only spot backtester with production sizing (research only).

Execution model:

* decisions are taken at the close of bar i and filled at the **open of bar i+1** (next bar);
* buys pay ``open x (1 + slippage)``, sells receive ``price x (1 - slippage)``; every fill pays
  the taker fee on its notional (0.1% per side by default);
* stops and targets are checked intrabar from the fill bar on: a bar whose low reaches the stop
  exits at the stop (or at the open when it gapped through it); a bar whose high reaches the
  target exits at the target (or the open). When both are inside one bar the stop wins
  (conservative). Time stops and signal exits fill at the next open;
* SHORT on spot only exits: the book never goes negative.

An entry must clear fusion's bar: strength >= 0.6, raised by up to 0.3 as the agent's equity
falls (services.agents.roster.entry_min_score). Sizing then mirrors production
(services.agents.fusion.build_candidate + roster.shape_for_agent):
risk budget = min(reference capital, account equity) x 0.25% x strength, divided by the stop
distance; then the agent shrinks it by ``(equity/200)^2`` and caps exposure at 1.0x of its own
equity; quantities floor to the lot size and must clear the minimum notional. An agent whose
equity falls below the elimination floor stops trading. BRL is converted at a fixed research
rate (``fx_brl_per_quote``); the agent capital is virtual.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime

from services.research.data import Bar
from services.research.strategies import Plan

DAY_MS = 86_400_000

# Production constants (services.execution.constitution / services.agents.roster).
REFERENCE_CAPITAL_BRL = 10_000.0
MAX_RISK_PER_POSITION = 0.0025
AGENT_CAPITAL_BRL = 200.0
ELIMINATION_FLOOR_BRL = 30.0
LOT_SIZE = {"BTCUSDT": 0.00001, "ETHUSDT": 0.0001, "SOLUSDT": 0.001}
MIN_NOTIONAL = {"BTCUSDT": 5.0, "ETHUSDT": 5.0, "SOLUSDT": 5.0}


@dataclass(frozen=True)
class CostModel:
    fee_rate: float = 0.001  # taker, per side
    slippage_bps: float = 2.0

    @property
    def slippage(self) -> float:
        return self.slippage_bps / 10_000


@dataclass(frozen=True)
class SizingModel:
    fx_brl_per_quote: float = 5.4  # fixed research rate, BRL per USDT
    agent_capital_brl: float = AGENT_CAPITAL_BRL
    account_equity_brl: float = REFERENCE_CAPITAL_BRL
    lot_size: float = 0.00001
    min_notional: float = 5.0
    # None: use the strategy's stop distance; a number: the pipeline's fixed stop distance.
    sizing_stop: float | None = 0.02
    # Fusion's entry bar (DEFAULT_FUSION_POLICY.min_score), raised by up to 0.3 as the agent's
    # equity falls (roster.entry_min_score). A lone signal's fusion score is its strength.
    fusion_min_score: float = 0.6
    survival_slope: float = 0.3

    def admits(self, strength: float, equity_quote: float) -> bool:
        ratio = min(max(equity_quote * self.fx_brl_per_quote / self.agent_capital_brl, 0.0), 1.0)
        bar = min(self.fusion_min_score + self.survival_slope * (1 - ratio), 1.0)
        return strength >= bar - 1e-12

    @classmethod
    def for_symbol(cls, symbol: str, **kw: float | None) -> SizingModel:
        return cls(
            lot_size=LOT_SIZE.get(symbol, 0.00001), min_notional=MIN_NOTIONAL.get(symbol, 5.0), **kw
        )  # type: ignore[arg-type]

    def quantity(
        self, price: float, strength: float, stop_frac: float, equity_quote: float, cash: float
    ) -> float:
        fx = self.fx_brl_per_quote
        equity_brl = equity_quote * fx
        sizing_equity = min(self.account_equity_brl, REFERENCE_CAPITAL_BRL)
        stop = self.sizing_stop if self.sizing_stop is not None else stop_frac
        risk_qty = sizing_equity * MAX_RISK_PER_POSITION * min(strength, 1.0) / fx / (price * stop)
        ratio = min(max(equity_brl / self.agent_capital_brl, 0.0), 1.0)
        qty = min(risk_qty * ratio * ratio, equity_quote / price, cash / price)
        qty = math.floor(qty / self.lot_size + 1e-9) * self.lot_size
        if qty <= 0 or qty * price < self.min_notional:
            return 0.0
        return qty


DEFAULT_COSTS = CostModel()
DEFAULT_SIZING = SizingModel()


@dataclass
class Trade:
    entry_index: int
    exit_index: int
    entry_price: float
    exit_price: float
    quantity: float
    fees: float
    pnl: float  # quote, net of fees
    reason: str

    @property
    def bars_held(self) -> int:
        return self.exit_index - self.entry_index + 1


@dataclass
class Result:
    start_equity: float
    equity: list[float]  # quote, marked at each bar close of the window
    times: list[int]  # bar open times of the window
    trades: list[Trade] = field(default_factory=list)
    fees: float = 0.0
    slippage_cost: float = 0.0
    bars_in_market: int = 0
    eliminated: bool = False

    @property
    def end_equity(self) -> float:
        return self.equity[-1] if self.equity else self.start_equity


def run(
    bars: Sequence[Bar],
    plan: Plan,
    *,
    start: int = 0,
    end: int | None = None,
    costs: CostModel = DEFAULT_COSTS,
    sizing: SizingModel = DEFAULT_SIZING,
    start_equity: float | None = None,
) -> Result:
    """Trade ``plan`` on ``bars[start:end]``; flat at the window's last close."""

    end = len(bars) if end is None else end
    if not 0 <= start < end <= len(bars):
        raise ValueError("bad window")
    fee, slip = costs.fee_rate, costs.slippage
    cash = (
        start_equity
        if start_equity is not None
        else sizing.agent_capital_brl / sizing.fx_brl_per_quote
    )
    result = Result(start_equity=cash, equity=[], times=[])
    qty = 0.0
    entry_price = entry_fee = stop_price = target_price = 0.0
    entry_index = 0
    pending: tuple[str, float, float, float] | None = (
        None  # ("buy", qty, stop, target) / ("sell",...)
    )
    pending_reason = ""
    eliminated = False

    def close_position(i: int, raw: float, reason: str) -> None:
        nonlocal cash, qty
        price = raw * (1 - slip)
        notional = qty * price
        exit_fee = notional * fee
        cash += notional - exit_fee
        result.fees += exit_fee
        result.slippage_cost += qty * raw * slip
        pnl = notional - exit_fee - qty * entry_price - entry_fee
        result.trades.append(
            Trade(entry_index, i, entry_price, price, qty, entry_fee + exit_fee, pnl, reason)
        )
        qty = 0.0

    for i in range(start, end):
        bar = bars[i]
        # 1) fill the order decided at the previous close, at this bar's open.
        if pending is not None:
            kind, p_qty, p_stop, p_target = pending
            if kind == "buy" and qty == 0:
                price = bar.open * (1 + slip)
                notional = p_qty * price
                cost = notional * (1 + fee)
                if cost > cash:  # the gap moved the price: shrink to what cash allows
                    p_qty = (
                        math.floor(cash / (price * (1 + fee)) / sizing.lot_size) * sizing.lot_size
                    )
                    notional = p_qty * price
                if p_qty > 0 and notional >= sizing.min_notional:
                    entry_fee = notional * fee
                    cash -= notional + entry_fee
                    result.fees += entry_fee
                    result.slippage_cost += p_qty * bar.open * slip
                    qty, entry_price, entry_index = p_qty, price, i
                    stop_price = price * (1 - p_stop)
                    target_price = price * (1 + p_target)
            elif kind == "sell" and qty > 0:
                close_position(i, bar.open, pending_reason)
            pending = None
        # 2) protective exits inside this bar.
        if qty > 0:
            if bar.low <= stop_price:
                close_position(i, min(bar.open, stop_price), "stop")
            elif bar.high >= target_price:
                close_position(i, max(bar.open, target_price), "target")
        equity = cash + qty * bar.close
        result.equity.append(equity)
        result.times.append(bar.open_time)
        if qty > 0:
            result.bars_in_market += 1
        if equity * sizing.fx_brl_per_quote < ELIMINATION_FLOOR_BRL:
            eliminated = True
        # 3) decide at this close, for the next open.
        if i >= end - 1:
            break
        if qty > 0:
            held = i - entry_index + 1
            if plan.exit[i]:
                pending, pending_reason = ("sell", qty, 0.0, 0.0), "signal"
            elif plan.max_bars is not None and held >= plan.max_bars:
                pending, pending_reason = ("sell", qty, 0.0, 0.0), "time"
        elif plan.entry[i] > 0 and not eliminated and sizing.admits(plan.entry[i], equity):
            q = sizing.quantity(bar.close, plan.entry[i], plan.stop[i], equity, cash)
            if q > 0:
                pending = ("buy", q, plan.stop[i], plan.target[i])
    if qty > 0:  # flat at the end of the window, at its last close
        close_position(end - 1, bars[end - 1].close, "window_end")
        result.equity[-1] = cash
    result.eliminated = eliminated
    return result


def buy_and_hold(
    bars: Sequence[Bar],
    *,
    start: int = 0,
    end: int | None = None,
    costs: CostModel = DEFAULT_COSTS,
    sizing: SizingModel = DEFAULT_SIZING,
    start_equity: float | None = None,
) -> Result:
    """Reference line: the whole agent equity bought at the first open, sold at the last close.

    Not a production rule (it ignores risk sizing); it answers "would holding have done better".
    """

    end = len(bars) if end is None else end
    cash = (
        start_equity
        if start_equity is not None
        else sizing.agent_capital_brl / sizing.fx_brl_per_quote
    )
    fee, slip = costs.fee_rate, costs.slippage
    price = bars[start].open * (1 + slip)
    q = math.floor(cash / (price * (1 + fee)) / sizing.lot_size) * sizing.lot_size
    notional = q * price
    rest = cash - notional * (1 + fee)
    result = Result(start_equity=cash, equity=[], times=[])
    for b in bars[start:end]:
        result.equity.append(rest + q * b.close)
        result.times.append(b.open_time)
    exit_price = bars[end - 1].close * (1 - slip)
    exit_notional = q * exit_price
    result.fees = (notional + exit_notional) * fee
    result.slippage_cost = q * (bars[start].open + bars[end - 1].close) * slip
    result.equity[-1] = rest + exit_notional * (1 - fee)
    result.bars_in_market = end - start
    result.trades.append(
        Trade(
            start,
            end - 1,
            price,
            exit_price,
            q,
            result.fees,
            result.equity[-1] - cash,
            "window_end",
        )
    )
    return result


# --- metrics -------------------------------------------------------------------------------


def daily_returns(
    equity: Sequence[float], times: Sequence[int], start_equity: float
) -> list[float]:
    """Close-to-close returns of the equity sampled at the last bar of each UTC day."""

    marks: list[float] = []
    day = None
    for value, t in zip(equity, times, strict=True):
        d = t // DAY_MS
        if d != day:
            marks.append(value)
            day = d
        else:
            marks[-1] = value
    prev = start_equity
    out = []
    for m in marks:
        out.append(m / prev - 1 if prev > 0 else 0.0)
        prev = m
    return out


def metrics(result: Result, *, bar_minutes: float | None = None) -> dict[str, float]:
    eq = result.equity
    if not eq:
        return {"trades": 0}
    start = result.start_equity
    total = eq[-1] / start - 1
    span_days = max((result.times[-1] - result.times[0]) / DAY_MS + (bar_minutes or 0) / 1440, 1e-9)
    cagr = (eq[-1] / start) ** (365 / span_days) - 1 if eq[-1] > 0 else -1.0
    peak, mdd = start, 0.0
    for v in eq:
        peak = max(peak, v)
        mdd = max(mdd, 1 - v / peak)
    rets = daily_returns(eq, result.times, start)
    sharpe = sortino = 0.0
    if len(rets) > 1:
        mean = sum(rets) / len(rets)
        var = sum((r - mean) ** 2 for r in rets) / (len(rets) - 1)
        if var > 0:
            sharpe = mean / math.sqrt(var) * math.sqrt(365)
        down = [min(r, 0.0) ** 2 for r in rets]
        dd = math.sqrt(sum(down) / len(rets))
        if dd > 0:
            sortino = mean / dd * math.sqrt(365)
    trades = result.trades
    wins = [t.pnl for t in trades if t.pnl > 0]
    losses = [t.pnl for t in trades if t.pnl <= 0]
    gross_loss = -sum(losses)
    return {
        "total_return": total,
        "cagr": cagr,
        "sharpe": sharpe,
        "sortino": sortino,
        "max_drawdown": mdd,
        "trades": float(len(trades)),
        "win_rate": len(wins) / len(trades) if trades else 0.0,
        "profit_factor": (sum(wins) / gross_loss)
        if gross_loss > 0
        else (math.inf if wins else 0.0),
        "expectancy": (sum(t.pnl for t in trades) / len(trades) / start) if trades else 0.0,
        "exposure": result.bars_in_market / len(eq),
        "fee_drag": (result.fees + result.slippage_cost) / start,
        "days": span_days,
        "eliminated": float(result.eliminated),
    }


def to_brl(quote: float, sizing: SizingModel = DEFAULT_SIZING) -> float:
    return quote * sizing.fx_brl_per_quote


def iso(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, tz=UTC).strftime("%Y-%m-%d")
