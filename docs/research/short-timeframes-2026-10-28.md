# EMA agents on 1-10 minute bars with a 3% stop (2026-10-28)

Research only: Binance public market data, virtual R$200 agents, no credentials, no order
authority. `financial_connectivity=false`, `real_money_execution=false`. Code:
`services/research/short_timeframes.py` (`python -m services.research.short_timeframes 120 out.json`).
Earlier, broader study: `docs/research/strategy-lab-2026-10-19.md`.

## Question

The operator asked for agents on 1-10 minute bars with a 3% stop, and for changes that make the
agents lose less. In production the own-strategy agents ran the EMA 5/20 cross on 1-minute bars,
without a protective stop, and lost money (LYRA −R$13.94, NOVA −R$9.52 on 2026-10-28).

## Data and method

- BTCUSDT, ETHUSDT and SOLUSDT, Binance spot 1m klines, 2026-06-30 → 2026-10-28 (120 days,
  172,800 bars per symbol, no gaps), resampled to 2, 3, 5 and 10 minutes.
- The lab backtester (`services/research/backtest.py`): decisions at the bar close, fills at the
  next open, stop and target checked inside the bar (the stop wins a tie), 3% stop, 6% target,
  sizing on the 3% stop and capped at the agent's R$200.
- **Selection on the first 60 days (train), results on the last 60 days (test).** Only test
  numbers count.
- Costs: **real** Binance taker fee (0.1% per side) + 2 bps slippage. The testnet charges no fee,
  so a testnet profit that real fees would erase is not a profit; the no-fee runs are kept only to
  show that gap.
- Variants: EMA pairs 5/20, 9/21, 12/26 and 20/50; a cooldown of 15 or 60 **bars** between
  signals; with or without a trend filter (entries only above the 1h EMA200).

## Results (test window, real costs)

Best configuration on train, per bar length, then its test result:

| Bar | BTC | ETH | SOL |
|---|---:|---:|---:|
| 1 min | −11.9% | −24.9% | −12.8% |
| 2 min | −14.0% | −2.3% | −3.1% |
| 3 min | −6.2% | −9.0% | −26.6% |
| 5 min | −14.2% | −9.6% | −13.7% |
| 10 min | +8.7% | +3.9% | −23.0% |

Buy & hold over the same test window: BTC +4.2%, ETH −3.4%, SOL −2.2%.

On 10-minute bars, the configuration production now runs (EMA 20/50, cooldown 60 bars = 600
minutes, 3% stop, 6% target, trend filter) against the alternatives:

| 10-minute bars, EMA 20/50 | BTC | ETH | SOL | Trades (test) |
|---|---:|---:|---:|---:|
| cooldown 60 min, trend filter | −1.7% | −11.7% | −10.4% | 25 / 27 / 32 |
| cooldown 600 min, no filter | +8.7% | −4.5% | −7.1% | 19 / 23 / 24 |
| **cooldown 600 min, trend filter** | **+7.4%** | **+4.1%** | **−8.4%** | 9 / 13 / 13 |
| same, without the 6% target | +7.3% | −1.0% | −8.1% | 9 / 13 / 13 |
| the old rule: EMA 5/20, 1-minute bars, cooldown 15, same stop and target | −39.5% | −51.5% | −61.3% | 220 / 271 / 355 |

## What it means

- **Short bars lose to fees.** At 1-5 minutes every best-on-train configuration lost on test
  with real costs. This confirms the 2026-10-19 study.
- **Trading less is what helps.** The 600-minute cooldown and the trend filter cut the trades by
  three to five times, and the losses with them.
- **No configuration is a proven edge.** The chosen one made money on two of three symbols over
  60 days, lost 8% on SOL, and was picked among 16 variants. It loses far less than the old rule,
  but it can still lose, and the sample is small. Judge it on forward results.

## What changed in production (PR "agents: 10-minute EMA 20/50, trend filter, target")

- `AGENT_EMA_BAR_MINUTES=10`, `AGENT_EMA_FAST=20`, `AGENT_EMA_SLOW=50`,
  `AGENT_EMA_COOLDOWN_MINUTES=600`.
- `AGENT_STOP_LOSS=0.03` (protective stop, PR #346) and `AGENT_TAKE_PROFIT=0.06`.
- `AGENT_EMA_TREND_FILTER=true` and, in the production image, `AGENT_EMA_WARMUP_KLINES=true`: bars
  are seeded from public klines at start (EMA 20/50 on 10-minute bars needs ~8 hours of bars), and
  entries need the mid above the 1h EMA200. A trend that cannot be read blocks entries, never exits.
- Sizing on the 3% stop (`AGENT_STOP_DISTANCE`, `RISK_STOP_DISTANCE`): only smaller orders.
