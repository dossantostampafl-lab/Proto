# Release readiness

This document is the consolidated release-readiness snapshot for Proto. It describes software readiness, not evidence of financial profitability.

## Safety boundary

Release candidates must preserve all of the following:

- no broker/exchange credentials;
- no wallets, custody, deposits or withdrawals;
- no real-money order routing;
- public live market functionality remains read-only;
- prediction-market research remains isolated from wagering/gambling services;
- API/UI safety surfaces continue to report real-money execution as disabled.

## Core pipeline status

### Data and microstructure

Implemented:

- canonical normalized `MarketTick`;
- sequence/timestamp/data-quality checks;
- stale-feed detection;
- feed-health states and risk permission degradation;
- order-book imbalance and microprice;
- temporal order-flow imbalance;
- liquidity/depth/spread-derived features.

### Quant research

Implemented:

- raw probability estimate;
- calibration and fair probability;
- confidence/uncertainty;
- fee/spread/slippage/latency/liquidity-aware edge;
- expected value;
- Hawkes state;
- synthetic Greeks;
- time-to-expiry exposure;
- hierarchical multi-timeframe trend/setup context;
- candidate decision that keeps probability estimation separate from trend vetoes.

### Risk

Implemented:

- fail-closed reconciliation state;
- working-order reservations;
- cumulative batch risk checks;
- position/notional/exposure limits;
- correlated/cluster exposure controls;
- liquidity/volatility/concentration/drawdown controls;
- kill-switch boundary;
- exact monetary arithmetic in Rust risk paths.

### Execution simulation

Implemented:

- order lifecycle;
- deterministic fill estimator;
- queue/flow/latency/spread-aware fill probability;
- GTC/IOC/FOK semantics;
- marketable limits;
- partial fills;
- FOK all-or-none behavior;
- resting-book execution price semantics;
- no live order submission.

### Replay

Implemented:

- deterministic caller-provided replay clock;
- explicit event phase ordering;
- anti-lookahead visibility boundary;
- per-stream monotonic sequence validation;
- duplicate-event protection;
- deterministic session fingerprint;
- API replay controls.

### Portfolio and P&L

Implemented:

- exact `Decimal` position ledger;
- long/short accounting;
- weighted average entry;
- partial close and position flips;
- realized/unrealized P&L;
- fees/net realized P&L;
- deterministic fill rebuild;
- position reconciliation.

### Statistical validation

Implemented:

- purged walk-forward splits;
- embargo;
- Sharpe/Sortino/max drawdown/hit rate/profit factor;
- fold consistency/robustness summary;
- Deflated Sharpe Ratio;
- CSCV Probability of Backtest Overfitting;
- deterministic block-bootstrap Monte Carlo;
- regime robustness;
- parameter stability/plateau diagnostics;
- research API endpoints;
- API-backed Validation Lab that starts empty and never displays fabricated performance metrics.

## Observability and operations

Implemented:

- request IDs and access logging;
- health/readiness endpoints;
- runtime and operation latency metrics;
- Prometheus surfaces;
- WebSocket health/capacity metrics;
- portfolio exposure/P&L gauges;
- explicit zero financial-connectivity metrics on the read-only live surface;
- PostgreSQL-backed optional journal and reconciliation paths;
- live-release contract gates in CI.

## Required quality gates

Every merge to a release candidate must keep these green:

- Python ruff;
- Python pytest;
- Rust fmt;
- Rust clippy with warnings denied;
- Rust workspace tests;
- web TypeScript typecheck;
- web production build;
- live-release contract checks;
- Python dependency audit;
- Rust dependency audit;
- web dependency audit;
- Graphify architecture workflow.

## Remaining empirical work before claiming validated alpha

These items do not block a software research RC, but they block any claim that a strategy has durable financial edge:

1. run the Validation Engine on sufficiently large historical/replay datasets;
2. record out-of-sample results by asset, horizon and regime;
3. compare multiple candidate strategies with PBO and DSR;
4. run delay, cost and execution-sensitivity experiments;
5. inspect Monte Carlo tail drawdowns and probability of loss;
6. require broad parameter plateaus rather than isolated optima;
7. accumulate paper-trading evidence under realistic feed degradation and fill assumptions;
8. version datasets, features, model parameters and validation outputs for reproducibility.

## RC decision rule

Proto may be called a **software research release candidate** when the current `main` passes the complete CI, Security and Graphify gates with the documented safety invariants intact.

Proto must not be described as a profitable or financially validated trading strategy solely because those software gates pass. Strategy validation is a separate empirical gate governed by the Validation Engine and recorded datasets.
