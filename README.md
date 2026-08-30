# Proto — Prediction Market Quant Engine

Research terminal for public live market data, deterministic simulation, paper trading, and
historical replay.

> Safety boundary: live access is public market data only. Proto never routes live orders and
> rejects broker/exchange credentials, deposits, withdrawals, custody, leverage, or real-money
> execution.

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
npm ci
npm run dev
```

Open `http://localhost:5173`. The API is available at `http://localhost:8000`.

## What is included

- probability, calibration, Hawkes, feature, sizing, hedge, and P&L research primitives;
- unauthenticated public Binance book tickers for BTCUSDT, ETHUSDT, and SOLUSDT;
- live feed normalization, validation, staleness, reconnect/backoff, and sequence-gap telemetry;
- deterministic Rust risk and simulated execution engines;
- simulated fills, portfolio accounting, optional PostgreSQL journal, and reconciliation;
- replay start, pause, resume, step, seek, speed, restart, and reset controls;
- WebSocket market data, order book, signal, risk, portfolio, fill, and analytics channels;
- request IDs, runtime metrics, liveness, readiness, and bounded WebSocket fan-out;
- Python, Rust, and web CI gates.

## Safety modes

Only these modes are valid:

- `LIVE_DATA_READ_ONLY` (default operational mode)
- `SIMULATION`
- `PAPER_TRADING`
- `HISTORICAL_REPLAY`

The API reports `real_money_execution: false` in risk and metrics responses. Live mode permits
only allowlisted public TLS endpoints and rejects private trading credentials at startup. The
kill switch stops live ingestion, simulation, and replay processing.

Start the public feed with:

```bash
curl -X POST http://localhost:8000/live/start \
  -H 'Content-Type: application/json' \
  -d '{"source":"binance","symbol":"BTCUSDT"}'
curl http://localhost:8000/live/status
```

## Operations and demo

- [Operations runbook](docs/OPERATIONS.md)
- [Five-minute demo](docs/DEMO.md)
- [Live-data architecture decision](docs/ADR-001-live-data-read-only.md)
- [Live-data quality and attack gates](docs/LIVE_DATA_QUALITY.md)

## Validation

```bash
ruff check apps services tests
python -m mypy --explicit-package-bases services/security/live_data_policy.py services/market_data/live.py apps/api/app/live.py
pytest
cargo fmt --all -- --check
cargo clippy --workspace --all-targets --all-features -- -D warnings
cargo test --workspace --all-features
cd apps/web && npm ci && npm run typecheck && npm run build && npm audit --audit-level=moderate
```

