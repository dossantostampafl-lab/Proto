# Strategy lab: why the R$200 agents bleed, and what to trade instead (2026-10-19)

Research only: Binance public market data, virtual R$200 agents, no credentials, no order
authority. `financial_connectivity=false`, `real_money_execution=false`. Code:
`services/research/` (`python -m services.research.lab download|backtest|walkforward|report`).

## TL;DR

- **The bleeding has one cause: the 1-minute EMA cross trades too often for its edge.** On
  test windows only, the current rule (EMA 5/20 on 1m) made 560–790 round trips in 8 months
  per symbol, won 18–22% of them (profit factor 0.25–0.37), and costs alone (0.1% taker per
  side + 2 bps slippage ≈ 0.24% per round trip) consumed 71–83% of the agent's R$200. Every
  agent reached the R$30 elimination floor (−75% to −85%).
- **Nothing on 1m, 5m or 15m survives costs.** Walk-forward found no configuration of any family
  that made money on a 120-day train window on 1m/5m, so it stayed flat on every test month.
- **No strategy beats costs *robustly* with statistical confidence.** One hourly configuration —
  regime-filtered time-series momentum — was positive after costs on the test windows of all
  three symbols (+20.5% BTC, +9.2% ETH, +14.9% SOL; PF 1.18–1.76; still positive at 10 bps
  slippage). But it was picked after looking at 288 configurations per symbol, and its
  deflated Sharpe ratio is ≈ 0 (≤ 0.05 even with a lenient trial variance). It is a
  **forward-test candidate, not a proven edge**.
- **Recommendation.** Turn the 1m EMA off for the agents now. If the agents must keep trading on
  testnet/paper, give the own-strategy slots the hourly regime-filtered momentum (wired behind
  `AGENT_LAB_STRATEGIES_ENABLED`, off by default) and judge it on forward results; TradingView
  slots should only take alerts from ≥ 1h charts (or be paused). Otherwise the honest answer is:
  trade less, or don't trade.

## Data

- Binance spot klines from `https://data-api.binance.vision/api/v3/klines` (public, read-only),
  BTCUSDT, ETHUSDT, SOLUSDT, 1m/5m/15m/1h, 2025-10-19 → 2026-10-18 (365 days; 525,600 1m bars
  per symbol, no gaps). 4h bars are resampled from 1h. Cached under the gitignored
  `data/research/` (or `$PROTO_RESEARCH_DATA`); never committed.
- The year splits into a bear half and a recovery: buy & hold over the whole year was −23% BTC,
  −38% ETH, −45% SOL; over the test span (2026-02-16 → 2026-10-13) it was +20% / +27% / +24%,
  with 29–38% drawdowns.

## Method

**Backtester** (`services/research/backtest.py`), event-driven, long-only spot:

- Decisions at the close of bar *i*, fills at the open of bar *i+1*. SHORT only sells holdings.
- Costs: 0.1% taker fee per side on every fill, 2 bps slippage against us (configurable).
- Stops and targets are checked intrabar from the fill bar on; a gap through the stop fills at
  the open; when stop and target are both inside one bar, the stop wins. Time stops and
  signal exits fill at the next open. Every position is flat at the end of a window.
- Production rules, as in `services/agents/fusion.py` and `services/agents/roster.py`: an entry
  needs fusion score ≥ 0.6 (a lone signal's score is its strength), raised by up to 0.3 as the
  agent's equity falls; quantity = R$10,000 × 0.25% × strength ÷ 2% stop (the pipeline's
  `agent_stop_distance`), then × (equity/200)², capped at 1.0× the agent's own equity, floored
  to the lot size, ≥ 5 USDT notional. BRL at a fixed research rate of 5.4 BRL/USDT. Below
  R$30 the agent is eliminated and stops entering. In practice every entry is the full
  R$200 notional: the risk budget (R$1,250 at a 2% stop) is always above the agent cap.
- Metrics: total return, CAGR, Sharpe and Sortino (daily, ×√365), max drawdown, win rate,
  profit factor, expectancy per trade (% of starting equity), trades, exposure, fee drag
  (fees + slippage as % of starting equity).

**Walk-forward** (`services/research/walkforward.py`): rolling 120-day train / 30-day test,
stepped by 30 days → 8 test months (2026-02-16 → 2026-10-13). Per window the best train
Sharpe among configurations with ≥ 3 train trades is chosen; if it lost money on train, the
agent **abstains** for that test month. Test months are chained (each starts with the equity
the last one ended with). Indicators are causal; a test asserts that rewriting all bars after a
test window changes nothing inside it. Only test-window numbers are reported.

**Deflation.** Every configuration counts as a trial (288 per symbol: 8 on 1m, 52 on 5m, 76 on
each of 15m, 1h, 4h). The Deflated Sharpe Ratio (Bailey & López de Prado) gives the probability
that a result's daily Sharpe beats the best Sharpe expected from that many skill-less trials,
given the dispersion of Sharpes actually observed. DSR ≥ 0.95 would be convincing; nothing
comes close.

**Baselines.** Buy & hold of the agent's whole equity over the test span; a random-entry trader
(enter with probability 1/(3 × 12h of bars) per bar, 12-hour time stop, 2% stop, 4% target),
mean of 20 seeds on the same test windows.

**Strategies** (`services/research/strategies.py`; each defines entry, exit, stop and target):

| Family | Rule | Grid |
|---|---|---|
| (a) `ema_cross` | The production rule: EMA fast/slow cross arms, fires when the gap opens to strength 0.6 (exit 0.2) within 10 bars, 15-min cooldown; 2% stop, 4% target | the roster's 8 (fast, slow) pairs |
| (b) `ema_trend` | EMA cross up while close > higher-timeframe EMA (1h EMA200 / 4h EMA50 / 1d EMA20); exit on cross down or close < HTF EMA; ATR stop/target | 12 |
| (c) `donchian` | Close > prior N-bar high on volume > k × 20-bar average; exit on close < prior M-bar low; ATR stop/target | 16 |
| (d) `connors_rsi` | Close > SMA200 and (RSI(2) < x or close < lower Bollinger); exit close > SMA5; time stop; ATR stop/target | 16 |
| (e) `ts_momentum` | Long while the 24h/72h/168h return (and optionally the 30-day return) is positive; strength = min(1, 50% vol target ÷ realized vol); 3-ATR stop, 6/12-ATR target | 12 |
| (f) `regime` | Wraps (e) or (c): entries only while ATR% is in the lower or upper half of its 30-day range | 12 |

"Robust" in the tables means all of: test return > 0, Sharpe > buy & hold's, return > random
entry's, profit factor > 1.1, ≥ 10 trades, max drawdown < 15%, ≥ 3 traded test months with at
least half of them positive.

## Results (test windows only, after costs)

`wf` rows are walk-forward with per-window selection (+0.0% with 0 trades = it abstained every
month because nothing made money on train). "+/traded/all windows" counts positive test months,
months with trades, and all months.

#### BTCUSDT

| TF | Strategy | OOS return | Max DD | Sharpe | Trades | Win | PF | Exp./trade | Exposure | Fee drag | +/traded/all windows | DSR | Robust |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|
| 1m | ema_cross (wf, 8 cfg) | +0.0% | 0.0% | 0.00 | 0 | 0% | 0.00 | +0.0% | 0% | 0.0% | 0/0/8 | 0.00 | no |
| 1m | buy & hold | +20.3% | 29.8% | 0.91 | 1 | | | | 100% | 0.3% | | | |
| 1m | random entry (mean of 20) | -25.8% | 31.4% | -2.43 | 122 | 39% | 0.63 | -0.2% | 23% | 24.9% | | | |
| 5m | ema_cross (wf, 8 cfg) | +0.0% | 0.0% | 0.00 | 0 | 0% | 0.00 | +0.0% | 0% | 0.0% | 0/0/8 | 0.00 | no |
| 5m | ema_trend (wf, 12 cfg) | +0.0% | 0.0% | 0.00 | 0 | 0% | 0.00 | +0.0% | 0% | 0.0% | 0/0/8 | 0.00 | no |
| 5m | donchian (wf, 16 cfg) | +0.0% | 0.0% | 0.00 | 0 | 0% | 0.00 | +0.0% | 0% | 0.0% | 0/0/8 | 0.00 | no |
| 5m | connors_rsi (wf, 16 cfg) | +0.0% | 0.0% | 0.00 | 0 | 0% | 0.00 | +0.0% | 0% | 0.0% | 0/0/8 | 0.00 | no |
| 5m | buy & hold | +20.3% | 29.4% | 0.91 | 1 | | | | 100% | 0.3% | | | |
| 5m | random entry (mean of 20) | -28.2% | 31.5% | -2.56 | 125 | 39% | 0.60 | -0.2% | 23% | 24.7% | | | |
| 15m | ema_cross (wf, 8 cfg) | +0.0% | 0.0% | 0.00 | 0 | 0% | 0.00 | +0.0% | 0% | 0.0% | 0/0/8 | 0.00 | no |
| 15m | ema_trend (wf, 12 cfg) | +0.0% | 0.0% | 0.00 | 0 | 0% | 0.00 | +0.0% | 0% | 0.0% | 0/0/8 | 0.00 | no |
| 15m | donchian (wf, 16 cfg) | +0.0% | 0.0% | 0.00 | 0 | 0% | 0.00 | +0.0% | 0% | 0.0% | 0/0/8 | 0.00 | no |
| 15m | connors_rsi (wf, 16 cfg) | +0.0% | 0.0% | 0.00 | 0 | 0% | 0.00 | +0.0% | 0% | 0.0% | 0/0/8 | 0.00 | no |
| 15m | ts_momentum (wf, 12 cfg) | +0.0% | 0.0% | 0.00 | 0 | 0% | 0.00 | +0.0% | 0% | 0.0% | 0/0/8 | 0.00 | no |
| 15m | regime (wf, 12 cfg) | +0.0% | 0.0% | 0.00 | 0 | 0% | 0.00 | +0.0% | 0% | 0.0% | 0/0/8 | 0.00 | no |
| 15m | buy & hold | +20.3% | 29.3% | 0.91 | 1 | | | | 100% | 0.3% | | | |
| 15m | random entry (mean of 20) | -24.3% | 29.5% | -2.23 | 126 | 40% | 0.65 | -0.2% | 24% | 25.5% | | | |
| 1h | ema_cross (wf, 8 cfg) | -2.0% | 9.8% | -0.23 | 23 | 30% | 0.90 | -0.1% | 9% | 5.3% | 1/2/8 | 0.00 | no |
| 1h | ema_trend (wf, 12 cfg) | -11.7% | 13.9% | -2.17 | 32 | 22% | 0.42 | -0.4% | 9% | 7.2% | 0/5/8 | 0.00 | no |
| 1h | donchian (wf, 16 cfg) | -1.8% | 5.5% | -0.42 | 18 | 39% | 0.83 | -0.1% | 3% | 4.3% | 0/1/8 | 0.00 | no |
| 1h | connors_rsi (wf, 16 cfg) | +0.0% | 0.0% | 0.00 | 0 | 0% | 0.00 | +0.0% | 0% | 0.0% | 0/0/8 | 0.00 | no |
| 1h | ts_momentum (wf, 12 cfg) | +3.3% | 7.8% | 0.49 | 8 | 25% | 1.87 | +0.4% | 7% | 2.0% | 1/1/8 | 0.00 | no |
| 1h | regime (wf, 12 cfg) | +2.0% | 14.0% | 0.25 | 43 | 33% | 1.07 | +0.0% | 23% | 9.9% | 4/6/8 | 0.00 | no |
| 1h | buy & hold | +20.3% | 29.0% | 0.91 | 1 | | | | 100% | 0.3% | | | |
| 1h | random entry (mean of 20) | -25.4% | 29.5% | -2.44 | 123 | 40% | 0.62 | -0.2% | 23% | 24.8% | | | |
| 4h | ema_cross (wf, 8 cfg) | +2.7% | 4.6% | 0.48 | 9 | 44% | 1.32 | +0.3% | 11% | 2.2% | 3/4/8 | 0.00 | no |
| 4h | ema_trend (wf, 12 cfg) | +1.4% | 4.5% | 0.23 | 11 | 45% | 1.15 | +0.1% | 16% | 2.6% | 3/5/8 | 0.00 | no |
| 4h | donchian (wf, 16 cfg) | -9.9% | 10.5% | -1.15 | 13 | 31% | 0.44 | -0.8% | 16% | 2.9% | 2/5/8 | 0.00 | no |
| 4h | connors_rsi (wf, 16 cfg) | -2.2% | 3.1% | -1.22 | 6 | 33% | 0.25 | -0.4% | 2% | 1.4% | 0/2/8 | 0.00 | no |
| 4h | ts_momentum (wf, 12 cfg) | -11.9% | 16.9% | -0.94 | 25 | 28% | 0.57 | -0.5% | 21% | 5.3% | 2/4/8 | 0.00 | no |
| 4h | regime (wf, 12 cfg) | -3.5% | 15.5% | -0.24 | 16 | 31% | 0.81 | -0.2% | 21% | 3.7% | 2/4/8 | 0.00 | no |
| 4h | buy & hold | +20.3% | 28.9% | 0.91 | 1 | | | | 100% | 0.3% | | | |
| 4h | random entry (mean of 20) | -25.3% | 30.0% | -2.39 | 122 | 39% | 0.62 | -0.2% | 22% | 24.6% | | | |

#### ETHUSDT

| TF | Strategy | OOS return | Max DD | Sharpe | Trades | Win | PF | Exp./trade | Exposure | Fee drag | +/traded/all windows | DSR | Robust |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|
| 1m | ema_cross (wf, 8 cfg) | +0.0% | 0.0% | 0.00 | 0 | 0% | 0.00 | +0.0% | 0% | 0.0% | 0/0/8 | 0.00 | no |
| 1m | buy & hold | +26.7% | 38.6% | 0.92 | 1 | | | | 100% | 0.3% | | | |
| 1m | random entry (mean of 20) | -24.8% | 32.3% | -1.87 | 124 | 41% | 0.70 | -0.2% | 21% | 25.8% | | | |
| 5m | ema_cross (wf, 8 cfg) | +0.0% | 0.0% | 0.00 | 0 | 0% | 0.00 | +0.0% | 0% | 0.0% | 0/0/8 | 0.00 | no |
| 5m | ema_trend (wf, 12 cfg) | +0.0% | 0.0% | 0.00 | 0 | 0% | 0.00 | +0.0% | 0% | 0.0% | 0/0/8 | 0.00 | no |
| 5m | donchian (wf, 16 cfg) | +0.0% | 0.0% | 0.00 | 0 | 0% | 0.00 | +0.0% | 0% | 0.0% | 0/0/8 | 0.00 | no |
| 5m | connors_rsi (wf, 16 cfg) | +0.0% | 0.0% | 0.00 | 0 | 0% | 0.00 | +0.0% | 0% | 0.0% | 0/0/8 | 0.00 | no |
| 5m | buy & hold | +26.7% | 38.5% | 0.92 | 1 | | | | 100% | 0.3% | | | |
| 5m | random entry (mean of 20) | -27.6% | 33.6% | -2.01 | 127 | 41% | 0.68 | -0.2% | 22% | 25.6% | | | |
| 15m | ema_cross (wf, 8 cfg) | +0.0% | 0.0% | 0.00 | 0 | 0% | 0.00 | +0.0% | 0% | 0.0% | 0/0/8 | 0.00 | no |
| 15m | ema_trend (wf, 12 cfg) | -6.9% | 11.5% | -1.37 | 18 | 22% | 0.48 | -0.4% | 2% | 4.1% | 1/2/8 | 0.00 | no |
| 15m | donchian (wf, 16 cfg) | +0.0% | 0.0% | 0.00 | 0 | 0% | 0.00 | +0.0% | 0% | 0.0% | 0/0/8 | 0.00 | no |
| 15m | connors_rsi (wf, 16 cfg) | +0.0% | 0.0% | 0.00 | 0 | 0% | 0.00 | +0.0% | 0% | 0.0% | 0/0/8 | 0.00 | no |
| 15m | ts_momentum (wf, 12 cfg) | +0.0% | 0.0% | 0.00 | 0 | 0% | 0.00 | +0.0% | 0% | 0.0% | 0/0/8 | 0.00 | no |
| 15m | regime (wf, 12 cfg) | -24.9% | 24.9% | -3.60 | 69 | 14% | 0.31 | -0.4% | 8% | 14.3% | 0/3/8 | 0.00 | no |
| 15m | buy & hold | +26.7% | 38.2% | 0.92 | 1 | | | | 100% | 0.3% | | | |
| 15m | random entry (mean of 20) | -23.6% | 32.0% | -1.76 | 128 | 42% | 0.72 | -0.2% | 22% | 26.2% | | | |
| 1h | ema_cross (wf, 8 cfg) | -9.9% | 20.4% | -0.65 | 58 | 28% | 0.83 | -0.2% | 19% | 13.6% | 1/5/8 | 0.00 | no |
| 1h | ema_trend (wf, 12 cfg) | -14.3% | 15.0% | -1.47 | 38 | 26% | 0.62 | -0.4% | 10% | 8.6% | 2/7/8 | 0.00 | no |
| 1h | donchian (wf, 16 cfg) | -4.2% | 9.8% | -0.58 | 10 | 10% | 0.63 | -0.4% | 4% | 2.4% | 0/1/8 | 0.00 | no |
| 1h | connors_rsi (wf, 16 cfg) | +0.0% | 0.0% | 0.00 | 0 | 0% | 0.00 | +0.0% | 0% | 0.0% | 0/0/8 | 0.00 | no |
| 1h | ts_momentum (wf, 12 cfg) | -17.8% | 18.3% | -1.38 | 44 | 18% | 0.48 | -0.4% | 13% | 9.2% | 0/2/8 | 0.00 | no |
| 1h | regime (wf, 12 cfg) | -25.4% | 26.8% | -1.99 | 41 | 17% | 0.44 | -0.6% | 15% | 8.1% | 2/5/8 | 0.00 | no |
| 1h | buy & hold | +26.7% | 37.9% | 0.92 | 1 | | | | 100% | 0.3% | | | |
| 1h | random entry (mean of 20) | -24.1% | 31.5% | -1.78 | 126 | 42% | 0.71 | -0.2% | 22% | 25.5% | | | |
| 4h | ema_cross (wf, 8 cfg) | -16.0% | 17.8% | -1.15 | 49 | 29% | 0.71 | -0.3% | 20% | 10.9% | 2/7/8 | 0.00 | no |
| 4h | ema_trend (wf, 12 cfg) | -17.0% | 19.5% | -1.35 | 15 | 13% | 0.46 | -1.1% | 21% | 3.3% | 1/5/8 | 0.00 | no |
| 4h | donchian (wf, 16 cfg) | -20.4% | 20.4% | -3.49 | 8 | 0% | 0.00 | -2.5% | 2% | 1.7% | 0/2/8 | 0.00 | no |
| 4h | connors_rsi (wf, 16 cfg) | -6.3% | 7.1% | -0.92 | 19 | 58% | 0.58 | -0.3% | 5% | 4.5% | 2/5/8 | 0.00 | no |
| 4h | ts_momentum (wf, 12 cfg) | -20.7% | 20.7% | -1.39 | 34 | 29% | 0.47 | -0.6% | 24% | 7.0% | 1/4/8 | 0.00 | no |
| 4h | regime (wf, 12 cfg) | -18.6% | 29.3% | -0.94 | 48 | 29% | 0.68 | -0.4% | 37% | 10.8% | 2/7/8 | 0.00 | no |
| 4h | buy & hold | +26.7% | 36.7% | 0.92 | 1 | | | | 100% | 0.3% | | | |
| 4h | random entry (mean of 20) | -26.1% | 32.9% | -2.05 | 125 | 41% | 0.67 | -0.2% | 20% | 25.1% | | | |

#### SOLUSDT

| TF | Strategy | OOS return | Max DD | Sharpe | Trades | Win | PF | Exp./trade | Exposure | Fee drag | +/traded/all windows | DSR | Robust |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|
| 1m | ema_cross (wf, 8 cfg) | +0.0% | 0.0% | 0.00 | 0 | 0% | 0.00 | +0.0% | 0% | 0.0% | 0/0/8 | 0.00 | no |
| 1m | buy & hold | +23.7% | 38.6% | 0.85 | 1 | | | | 100% | 0.3% | | | |
| 1m | random entry (mean of 20) | -26.7% | 34.5% | -1.92 | 125 | 41% | 0.72 | -0.2% | 21% | 25.6% | | | |
| 5m | ema_cross (wf, 8 cfg) | +0.0% | 0.0% | 0.00 | 0 | 0% | 0.00 | +0.0% | 0% | 0.0% | 0/0/8 | 0.00 | no |
| 5m | ema_trend (wf, 12 cfg) | +0.0% | 0.0% | 0.00 | 0 | 0% | 0.00 | +0.0% | 0% | 0.0% | 0/0/8 | 0.00 | no |
| 5m | donchian (wf, 16 cfg) | +0.0% | 0.0% | 0.00 | 0 | 0% | 0.00 | +0.0% | 0% | 0.0% | 0/0/8 | 0.00 | no |
| 5m | connors_rsi (wf, 16 cfg) | +0.0% | 0.0% | 0.00 | 0 | 0% | 0.00 | +0.0% | 0% | 0.0% | 0/0/8 | 0.00 | no |
| 5m | buy & hold | +23.7% | 38.5% | 0.85 | 1 | | | | 100% | 0.3% | | | |
| 5m | random entry (mean of 20) | -29.3% | 35.2% | -2.06 | 128 | 40% | 0.69 | -0.2% | 21% | 25.4% | | | |
| 15m | ema_cross (wf, 8 cfg) | +0.0% | 0.0% | 0.00 | 0 | 0% | 0.00 | +0.0% | 0% | 0.0% | 0/0/8 | 0.00 | no |
| 15m | ema_trend (wf, 12 cfg) | +0.0% | 0.0% | 0.00 | 0 | 0% | 0.00 | +0.0% | 0% | 0.0% | 0/0/8 | 0.00 | no |
| 15m | donchian (wf, 16 cfg) | +0.0% | 0.0% | 0.00 | 0 | 0% | 0.00 | +0.0% | 0% | 0.0% | 0/0/8 | 0.00 | no |
| 15m | connors_rsi (wf, 16 cfg) | +0.0% | 0.0% | 0.00 | 0 | 0% | 0.00 | +0.0% | 0% | 0.0% | 0/0/8 | 0.00 | no |
| 15m | ts_momentum (wf, 12 cfg) | -2.1% | 10.3% | -0.23 | 11 | 36% | 0.83 | -0.2% | 2% | 2.5% | 0/1/8 | 0.00 | no |
| 15m | regime (wf, 12 cfg) | -4.8% | 17.3% | -0.26 | 53 | 23% | 0.86 | -0.1% | 8% | 12.4% | 2/3/8 | 0.00 | no |
| 15m | buy & hold | +23.7% | 38.3% | 0.85 | 1 | | | | 100% | 0.3% | | | |
| 15m | random entry (mean of 20) | -19.3% | 30.8% | -1.26 | 129 | 42% | 0.80 | -0.1% | 21% | 27.1% | | | |
| 1h | ema_cross (wf, 8 cfg) | -9.0% | 17.0% | -0.51 | 81 | 30% | 0.87 | -0.1% | 20% | 18.8% | 1/5/8 | 0.00 | no |
| 1h | ema_trend (wf, 12 cfg) | -7.9% | 15.1% | -0.51 | 38 | 21% | 0.79 | -0.2% | 9% | 8.6% | 3/5/8 | 0.00 | no |
| 1h | donchian (wf, 16 cfg) | +16.7% | 11.3% | 1.04 | 28 | 46% | 1.55 | +0.6% | 8% | 7.8% | 2/2/8 | 0.00 | no |
| 1h | connors_rsi (wf, 16 cfg) | -6.7% | 9.5% | -1.17 | 22 | 55% | 0.49 | -0.3% | 1% | 5.1% | 0/2/8 | 0.00 | no |
| 1h | ts_momentum (wf, 12 cfg) | +0.0% | 0.0% | 0.00 | 0 | 0% | 0.00 | +0.0% | 0% | 0.0% | 0/0/8 | 0.00 | no |
| 1h | regime (wf, 12 cfg) | +23.1% | 15.4% | 1.33 | 27 | 41% | 1.70 | +0.9% | 10% | 7.0% | 3/5/8 | 0.00 | no |
| 1h | buy & hold | +23.7% | 38.0% | 0.85 | 1 | | | | 100% | 0.3% | | | |
| 1h | random entry (mean of 20) | -26.6% | 32.7% | -1.79 | 127 | 41% | 0.72 | -0.2% | 21% | 25.2% | | | |
| 4h | ema_cross (wf, 8 cfg) | -3.1% | 13.5% | -0.31 | 15 | 33% | 0.85 | -0.2% | 5% | 3.5% | 3/4/8 | 0.00 | no |
| 4h | ema_trend (wf, 12 cfg) | +5.4% | 5.0% | 0.80 | 4 | 25% | 2.27 | +1.3% | 4% | 0.9% | 1/3/8 | 0.00 | no |
| 4h | donchian (wf, 16 cfg) | +5.1% | 18.3% | 0.44 | 8 | 38% | 1.26 | +0.6% | 7% | 2.1% | 1/2/8 | 0.00 | no |
| 4h | connors_rsi (wf, 16 cfg) | -0.9% | 2.7% | -0.50 | 4 | 50% | 0.62 | -0.2% | 1% | 1.0% | 0/1/8 | 0.00 | no |
| 4h | ts_momentum (wf, 12 cfg) | +25.2% | 19.1% | 1.18 | 27 | 30% | 1.66 | +0.9% | 20% | 6.7% | 3/4/8 | 0.00 | no |
| 4h | regime (wf, 12 cfg) | +0.9% | 17.5% | 0.18 | 20 | 30% | 1.03 | +0.0% | 17% | 4.5% | 3/4/8 | 0.00 | no |
| 4h | buy & hold | +23.7% | 37.2% | 0.85 | 1 | | | | 100% | 0.3% | | | |
| 4h | random entry (mean of 20) | -27.7% | 33.6% | -1.91 | 127 | 41% | 0.70 | -0.2% | 19% | 24.9% | | | |

#### Fixed configurations positive on every symbol (test windows, no per-window selection)

Chosen *after* seeing the test results, so they are deflated by every configuration tried on the symbol (DSR) and re-run with 10 bps slippage and over the full year.

| TF | Config | Symbol | OOS return | Max DD | Trades | PF | OOS @10bps | Full year | Full-year DD | DSR |
|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|
| 1h | regime[0.5-1](ts_momentum(168,0,12)) | BTCUSDT | +11.2% | 11.2% | 59 | 1.27 | +2.1% | -0.4% | 14.3% | 0.00 |
| 1h | regime[0.5-1](ts_momentum(168,0,12)) | ETHUSDT | +0.7% | 18.4% | 63 | 1.01 | -7.3% | -18.1% | 27.3% | 0.00 |
| 1h | regime[0.5-1](ts_momentum(168,0,12)) | SOLUSDT | +27.8% | 15.9% | 57 | 1.38 | +16.0% | +17.9% | 31.6% | 0.00 |
| 1h | regime[0.5-1](ts_momentum(168,720,12)) | BTCUSDT | +20.5% | 9.7% | 38 | 1.76 | +14.6% | +0.7% | 13.3% | 0.00 |
| 1h | regime[0.5-1](ts_momentum(168,720,12)) | ETHUSDT | +9.2% | 18.4% | 46 | 1.18 | +2.9% | -8.8% | 19.1% | 0.00 |
| 1h | regime[0.5-1](ts_momentum(168,720,12)) | SOLUSDT | +14.9% | 18.4% | 36 | 1.28 | +10.1% | +10.5% | 25.4% | 0.00 |
| 1h | regime[0.5-1](donchian(20,10,1,10)) | BTCUSDT | +11.3% | 16.8% | 46 | 1.38 | -0.6% | +0.7% | 18.8% | 0.00 |
| 1h | regime[0.5-1](donchian(20,10,1,10)) | ETHUSDT | +11.1% | 15.6% | 41 | 1.25 | +5.1% | -14.7% | 31.4% | 0.00 |
| 1h | regime[0.5-1](donchian(20,10,1,10)) | SOLUSDT | +41.3% | 14.6% | 47 | 1.85 | +32.6% | +16.8% | 25.0% | 0.00 |

#### Current production rule (EMA 5/20, 1m, fixed params, test windows only)

| Symbol | OOS return | Max DD | Trades | Win | PF | Fee drag |
|---|---:|---:|---:|---:|---:|---:|
| BTCUSDT | -75.5% | 75.5% | 561 | 18% | 0.25 | 70.8% |
| ETHUSDT | -85.0% | 85.0% | 675 | 21% | 0.29 | 75.7% |
| SOLUSDT | -85.1% | 85.2% | 785 | 22% | 0.37 | 82.8% |

## Summary per symbol (test span 2026-02-16 → 2026-10-13, after costs)

| Symbol | Current rule (EMA 5/20, 1m) | Best walk-forward row | Candidate: 1h regime TS-momentum (fixed) | Random entry (1h) | Buy & hold |
|---|---:|---:|---:|---:|---:|
| BTCUSDT | −75.5% (DD 75.5%, 561 trades) | 1h ts_momentum +3.3% (8 trades) | +20.5% (DD 9.7%, 38 trades, PF 1.76) | −25.4% | +20.3% (DD 29%) |
| ETHUSDT | −85.0% (eliminated, 675 trades) | 1h donchian −4.2% (every walk-forward row loses) | +9.2% (DD 18.4%, 46 trades, PF 1.18) | −24.1% | +26.7% (DD 38%) |
| SOLUSDT | −85.1% (eliminated, 785 trades) | 4h ts_momentum +25.2% (DD 19%); 1h regime +23.1% (DD 15.4%) | +14.9% (DD 18.4%, 36 trades, PF 1.28) | −26.6% | +23.7% (DD 38%) |

Monthly test returns of the candidate (Feb → Oct): BTC +2.4, +4.7, +1.9, 0.0, +2.4, −1.6,
+9.5, −0.1%; ETH +8.8, +3.4, −8.8, 0.0, +6.4, −4.5, +8.0, −3.1%; SOL +0.8, −0.3, −10.4,
−1.2, −3.7, −0.7, +18.9, +13.6%. SOL's result comes from the last two months. Over the whole
year (its first four months were the 120-day warm-up/train period, a bear market) it returned
+0.7% BTC, −8.8% ETH, +10.5% SOL against buy & hold −23%, −38%, −45%.

Findings:

1. **Frequency is the killer.** At R$200 per trade a round trip costs ~R$0.48. Any rule that
   trades more than about once a day needs an average gross edge above 0.24% per trade; none of
   the intraday rules here has one. The random-entry trader loses ~25% in 8 months from costs
   alone — the current 1m EMA does three times worse than random.
2. **Mean reversion (Connors RSI(2)) and plain EMA/Donchian systems did not survive costs on any
   timeframe** with walk-forward selection.
3. **The only family with a consistent sign across all three symbols was slow trend-following
   (7-day / 30-day momentum) taken only in the high-volatility half of the regime**, with a wide
   12-ATR take-profit and exits mostly by signal. That is a known, economically plausible
   effect (crypto time-series momentum), which is why it is the candidate — not because its
   numbers are significant. They are not (DSR ≈ 0).
4. Per-window selection did *worse* than fixed slow parameters: 120 days is too short to pick
   parameters reliably; the selector chased the last regime.
5. The fusion bar (score ≥ 0.6) interacts with volatility targeting: when realized volatility is
   above ~83% a year, the momentum strength falls below 0.6 and the entry is refused (seen on
   4h SOL/ETH). In production that is a silent abstention, which is acceptable but worth knowing.

## Recommendation per agent slot

| Slot | Recommendation |
|---|---|
| `proto-ema-cross-1m` on BTCUSDT, ETHUSDT, SOLUSDT | **Stop the 1m EMA** (it is the source of the losses). If the agents should keep trading on testnet/paper, enable `AGENT_ROSTER_ENABLED`, `AGENT_LAB_STRATEGIES_ENABLED` and `AGENT_LAB_WARMUP_KLINES`: newly spawned own-strategy agents then trade `LAB_HOURLY_TS_MOMENTUM` on 1h bars — `regime(low=0.5, high=1.0, window=720, atr 14)` around `ts_momentum(lookback=168, confirm_lookback=720, vol_period=72, target_vol=0.5, stop_atr=3, target_atr=12)`. Same parameters for all three symbols (per-symbol tuning would be fitting noise). ETH is the weakest (full-year −8.8%, DD 18%); if only two agents may trade, drop ETH. Expect ~4–6 trades a month per symbol and long flat stretches. |
| `tradingview` on BTCUSDT, ETHUSDT, SOLUSDT | The lab cannot evaluate alerts it does not generate. The same cost arithmetic applies: only send alerts from ≥ 1h charts, aim for ≤ ~1 trade/day, and keep `min_strength` ≥ 0.6. Ideally port the candidate rule to Pine on the 1h chart so both feeds can be compared. Otherwise pause these slots. |

Go/no-go for the candidate after a forward test (paper/testnet, ≥ 2 months): keep it only if
forward profit factor > 1.1 and drawdown < 15% per agent; otherwise turn the flag off. It never
raises a limit: it only produces signals for fusion, the reviewer, the Risk Engine permit and
the OMS.

## Caveats

- **Overfitting.** 288 configurations per symbol were tried; the candidate was chosen after
  seeing test results. Deflation says its Sharpe is indistinguishable from the best of 288 random
  tries. Positive numbers here are a hypothesis.
- **Short sample, one regime cycle.** 12 months, 8 test months, ~40 trades per symbol. One or two
  months drive most of the result (SOL especially). Regime changes can erase it.
- **Fills.** Backtest fills at the next bar's open plus 2 bps; testnet fills (IOC limit orders,
  thin testnet books, the Risk Engine's 50 bps collar, 5-second mid polling, stops that are
  signals rather than resting orders) will differ, and testnet prices are not mainnet prices.
- **Sizing.** The fixed 5.4 BRL/USDT rate ignores FX moves; the agent's virtual R$200 is a sizing
  budget, not money. Stops are 3 ATR for exits but sizing uses the pipeline's fixed 2% stop
  distance, as production does.
- **Live adapter.** Its "holding" state assumes its LONG was filled; mids carry no volume, so
  volume-based rules (Donchian) never enter live; without warm-up it needs ~30 days of mids
  before the first signal. A parity check on the last 2,000 BTC hours gave the same 23 entries
  as the backtest.
- No strategy guarantees no losses. The best evidence-based way to stop bleeding is to trade
  far less often.

## Reproduce

```bash
python -m services.research.lab download                 # public data, cached; the 1m series take longest
python -m services.research.lab walkforward --processes 4  # ~1 min
python -m services.research.lab report                     # these tables
python -m services.research.lab backtest --symbol SOLUSDT --timeframe 1h \
  --spec '{"strategy": "ts_momentum", "lookback": 168, "confirm_lookback": 720}'
```

Results JSON: `data/research/results/walkforward.json` (gitignored).
