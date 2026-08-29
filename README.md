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

- probability, calibration, Hawkes, feature, sizing, hedge, and P&L research primitives;
- deterministic Rust risk and simulated execution engines;
- simulated fills, portfolio accounting, optional PostgreSQL journal, and reconciliation;
- replay start, pause, resume, step, seek, speed, restart, and reset controls;
- WebSocket market data, order book, signal, risk, portfolio, fill, and analytics channels;
- request IDs, runtime metrics, liveness, readiness, and bounded WebSocket fan-out;
- Python, Rust, and web CI gates.

## Safety modes

Only these modes are valid:

- `SIMULATION`
- `PAPER_TRADING`
- `HISTORICAL_REPLAY`

The API reports `real_money_execution: false` in risk and metrics responses. The kill switch
halts simulation and replay processing.

## Operations and demo

- [Operations runbook](docs/OPERATIONS.md)
- [Five-minute demo](docs/DEMO.md)

## Validation

```bash
ruff check apps services tests
pytest
cargo fmt --all -- --check
cargo clippy --workspace --all-targets --all-features -- -D warnings
cargo test --workspace --all-features
cd apps/web && npm run build
```
