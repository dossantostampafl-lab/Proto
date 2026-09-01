# Proto — Prediction Market Quant Engine

Research terminal for deterministic market simulation, paper trading, and historical replay.

> Safety boundary: Proto never routes live orders and does not accept broker/exchange
> credentials, deposits, withdrawals, custody, leverage, or real-money execution.

## Quick start

Requirements: Python 3.13+, Node 22+, and Rust 1.85.

```bash
python -m pip install -e '.[dev]'
pytest
uvicorn apps.api.app.main:app --reload
```

In another terminal:

```bash
cd apps/web
npm install
npm run dev
```

Open `http://localhost:5173`. The API is available at `http://localhost:8000`.

## What is included

- probability, calibration, Hawkes, feature, sizing, hedge, Greeks, markout and P&L research primitives;
- deterministic Rust risk gates with reservations, cumulative batch controls and volatility gating;
- risk-admitted simulated execution: orders cannot become validated before the Rust risk gate approves them;
- simulated fills, temporal portfolio exposure, P&L attribution, optional PostgreSQL journal, recovery and reconciliation;
- replay start, pause, resume, step, seek, speed, restart and reset controls with deterministic replay/fill clocks;
- WebSocket market data, order book, signal, risk, portfolio, fill and analytics channels;
- multi-asset terminal telemetry for lifecycle, Greeks, Hawkes, expiry, positions, P&L and temporal exposure;
- purged walk-forward, DSR, PBO, CPCV, White Reality Check, Hansen SPA, frozen holdout, Monte Carlo, regime and parameter-stability validation;
- fail-closed model promotion requiring recorded validation evidence;
- API-backed Validation Lab with no fabricated performance telemetry;
- request IDs, runtime/portfolio/temporal metrics, liveness, readiness and bounded WebSocket fan-out;
- Python, Rust, web, security, live-release and Graphify CI gates.

## Safety modes

Only these modes are valid:

- `SIMULATION`
- `PAPER_TRADING`
- `HISTORICAL_REPLAY`

The API reports `real_money_execution: false` in risk and metrics responses. The kill switch
halts simulation and replay processing.

## Operations and release evidence

- [Operations runbook](docs/OPERATIONS.md)
- [Five-minute demo](docs/DEMO.md)
- [Release readiness](docs/RELEASE_READINESS.md)
- [Benchmark provenance](docs/BENCHMARKS.md)

## Validation

```bash
ruff check apps services tests
pytest
cargo fmt --all -- --check
cargo clippy --workspace --all-targets --all-features -- -D warnings
cargo test --workspace --all-features
cd apps/web && npm run typecheck && npm run build
```

Passing software gates establishes software/research readiness only. Claims about durable financial
edge require separate out-of-sample evidence produced and recorded through the Validation Engine.
