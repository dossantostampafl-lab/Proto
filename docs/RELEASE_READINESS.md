# Release readiness

This document is the consolidated release-readiness snapshot for Proto. It describes **software research readiness**, not evidence of financial profitability.

## Safety boundary

Release candidates must preserve all of the following:

- no broker/exchange credentials;
- no wallets, custody, deposits or withdrawals;
- no real-money order routing;
- public live market functionality remains read-only;
- prediction-market research remains isolated from wagering/gambling execution services;
- API/UI safety surfaces continue to report real-money execution as disabled.

## Core pipeline status

### Data and microstructure

Implemented:

- canonical normalized market ticks and sequencing checks;
- source/receive timestamp and stale-feed controls;
- HEALTHY/DEGRADED/STALE/DARK/RECOVERING feed-health ladder;
- clean-update probation before restoring healthy risk state;
- imbalance, microprice, temporal OFI and normalized OFI;
- VPIN/toxicity;
- spread/depth/liquidity features;
- L2 post-fill markout and adverse-selection analytics with reconnect-generation isolation;
- frozen offline public-wire golden fixture with provenance.

### Quant research

Implemented:

- raw/calibrated/fair probability;
- confidence and uncertainty;
- Brier, log-loss, reliability and calibration-error metrics;
- fee/spread/slippage/latency/liquidity-aware edge;
- expected value;
- Hawkes state;
- synthetic Greeks and time exposure;
- fixed-fractional, volatility-adjusted, edge-adjusted and capped-Kelly research sizing;
- hierarchical HTF/MTF/LTF trend, setup and trigger context;
- trend veto remains separate from probability estimation.

### Risk

Implemented:

- fail-closed reconciliation readiness;
- non-finite-state rejection;
- working-order reservations;
- cumulative atomic batch risk;
- position/notional/gross/correlated exposure controls;
- liquidity, volatility, concentration and drawdown controls;
- market-specific volatility gating in both stateless and reservation-aware paths;
- strictly risk-reducing close semantics during volatility spikes;
- execution price collar;
- kill switch;
- exact Rust monetary arithmetic.

### Execution simulation

Implemented:

- deterministic order lifecycle and fill estimation;
- GTC/IOC/FOK, marketable limits and partial fills;
- resting-book price semantics;
- L2 queue-position/depletion model;
- explicit latency and fee friction;
- adverse side-aware tick-grid rounding;
- Decimal fill-grid/notional/fee arithmetic on the active Python simulator path;
- depth/participation-sensitive market impact;
- reservation-aware Rust execution admission: an order cannot become `Validated` until risk and volatility gates approve it;
- immediate working-order capacity reservation after execution admission;
- no live order submission.

### Replay and persistence

Implemented:

- deterministic caller-provided replay clock;
- ordered event phases and monotonic stream sequencing;
- anti-lookahead visibility boundary;
- duplicate-event protection and deterministic fingerprint;
- API replay wired to the deterministic replay core;
- historical simulation validates snapshots against replay time rather than wall-clock time;
- simulated replay fills inherit replay time rather than wall-clock time;
- replay start/restart/seek isolate in-memory portfolio state;
- replay start/restart/seek rotate persistent SQL journal sessions before timeline mutation;
- persisted fill timestamps are restored as timezone-aware UTC values;
- persistence-isolation failures are fail-closed.

### Portfolio and P&L

Implemented:

- active `Decimal` position ledger;
- long/short, average entry, partial close and flips;
- realized/unrealized/net P&L and fees;
- turnover/exposure accounting;
- deterministic position-open and last-fill clocks;
- time-weighted notional exposure and maximum position-age accounting;
- deterministic fill rebuild and reconciliation;
- canonical P&L attribution surface using directly observed fees/slippage only;
- unresolved P&L attribution remains an explicit residual rather than fabricated model/market components.

### Statistical validation

Implemented:

- purged walk-forward and embargo;
- CPCV;
- Sharpe, Sortino, drawdown, hit rate and profit factor;
- Deflated Sharpe Ratio;
- CSCV Probability of Backtest Overfitting;
- deterministic block-bootstrap Monte Carlo;
- regime robustness and parameter stability;
- White Reality Check and Hansen SPA family-level validation;
- effective independent/correlated trial accounting;
- one-shot frozen holdout evidence and fail-closed model-promotion gates;
- delay-injection and timestamp-shuffle negative controls;
- research API surfaces and API-backed Validation Lab with no fabricated metrics.

## Frontend telemetry

Implemented:

- canonical REST reconciliation plus bounded WebSocket streaming;
- market data, order book, lifecycle, Greeks, Hawkes, expiry, positions, fills and portfolio telemetry;
- temporal exposure and maximum position age surfaced from the canonical portfolio;
- canonical P&L attribution displayed with fees, slippage and unexplained residual;
- Greeks/Hawkes target selection follows lifecycle/active streamed market context instead of fixed BTC requests;
- no fabricated performance telemetry or live-execution controls.

## Observability and operations

Implemented:

- request IDs/access logs;
- health/readiness endpoints;
- latency/runtime/portfolio metrics;
- Prometheus/WebSocket observability;
- Prometheus gauges for time-weighted portfolio exposure and maximum position age;
- explicit zero financial-connectivity metrics on live read-only surfaces;
- PostgreSQL optional journal/recovery/reconciliation paths;
- live-release contract gates.

## Required release gauntlet

A research release candidate requires all of these to pass on the same current head/base state:

- Python Ruff;
- complete Python pytest suite;
- targeted mutation-safety gate;
- Rust fmt;
- Rust clippy with warnings denied;
- Rust workspace tests;
- Web TypeScript typecheck;
- Web production build;
- live-release/recovery contract gates;
- Python/Rust/Web dependency security audits;
- Graphify architecture workflow.

## Software-scope closure

For the current research/software scope:

- the approved high-value benchmark backlog has no remaining `ADOPT` items;
- repository searches contain no declared `TODO`, `FIXME` or `NotImplemented` implementation placeholders;
- the active quant, risk, simulated-execution, replay, persistence, portfolio, observability and web telemetry paths are connected through tested contracts;
- release qualification remains gate-driven: documentation never overrides a failing CI, Security or Graphify result.

## Benchmark adoption status

The approved high-value benchmark backlog is complete for the current research scope. The benchmark matrix contains no remaining `ADOPT` items. Remaining `DEFER` entries are non-RC research extensions; `REJECT` entries are intentional safety/licensing exclusions.

## Remaining empirical work before claiming validated alpha

These items do not block a software research RC, but they block any claim of durable financial edge:

1. run the Validation Engine on sufficiently large historical/replay datasets;
2. record out-of-sample results by asset, horizon and regime;
3. compare multiple candidate strategies with PBO/DSR/CPCV and family-level corrections;
4. run delay, cost, queue and impact sensitivity experiments;
5. inspect Monte Carlo tail drawdowns and probability of loss;
6. require broad parameter plateaus rather than isolated optima;
7. accumulate paper-trading evidence under realistic feed degradation and fills;
8. version datasets, features, parameters and validation outputs for reproducibility.

## RC decision rule

Proto may be called a **software research release candidate** only when the current `main` passes the complete CI, Security and Graphify gauntlet with all documented safety invariants intact.

Passing the software gauntlet is not a profitability claim. Strategy/alpha validation remains a separate empirical gate.
