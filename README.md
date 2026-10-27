# Proto — Prediction Market Quant Engine

Research terminal for deterministic market simulation, paper trading, and historical replay.

> Safety boundary: Proto never routes real-money orders. Execution is limited to paper, the
> Binance Spot **testnet**, the XP/B3 **demo** account and (opt-in) the Alpaca US **paper** account,
> always through a Risk Permit consumed by the OMS. Testnet/demo/paper credentials live only in the
> isolated executor (`apps/executor`), never in the API. Deposits, withdrawals, custody, leverage
> above 1.0x and changes to the constitutional limits are outside the system (`AGENTS.md`).

## Quick start

Requirements: Python 3.13+ and Node 22+.

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

- probability, calibration, Hawkes, feature, sizing, Greeks, markout and P&L research primitives;
- one server-side risk gate (`apps/api/app/simulation.py`): kill switch, order/position notional,
  position quantity, open positions, exposure, concentration, drawdown, volatility, liquidity and
  slippage limits; every paper order passes it inside one serialized critical section;
- simulated fills, temporal portfolio exposure, P&L attribution, optional PostgreSQL journal, recovery and reconciliation;
- replay start, pause, resume, step, seek, speed, restart and reset controls with deterministic replay/fill clocks;
- WebSocket market data, order book, signal, risk, portfolio, fill and analytics channels;
- multi-asset terminal telemetry for lifecycle, Greeks, Hawkes, expiry, positions, P&L and temporal exposure;
- purged walk-forward, DSR, PBO, CPCV, White Reality Check, Hansen SPA, frozen holdout, Monte Carlo, regime and parameter-stability validation;
- fail-closed model promotion requiring recorded validation evidence;
- request IDs, runtime/portfolio/temporal metrics, liveness, readiness and bounded WebSocket fan-out;
- HUD dashboard (Command · Trade · Lab · Replay · System) with explicit data provenance — see [DESIGN.md](DESIGN.md);
- Python, web, compose, security and Graphify CI gates.

## Architecture

| Entry point | Purpose |
|---|---|
| `apps.api.app.main:app` | The application: research, simulation, paper, replay, live monitor, operator auth and the built dashboard (`apps/web/dist`) on one origin. |
| `apps.api.app.live_app:app` | Import-isolated, GET-only public monitor (`Dockerfile.api`, `docker-compose.live.yml`). It cannot load simulation or execution code. |

Core research/simulation routes live in `apps/api/app/core_routes.py`; everything else is a router included by `main.py`.

## Deploy (Oracle Cloud)

The root `Dockerfile` builds the dashboard and the API into one image. Deploys are pull-based: the
`Deploy` workflow only publishes `ghcr.io/<owner>/proto:stable` after a green CI run on `main`, and
moves the executor's `:executor` tag only after an operator approves the `executor` environment.
Each Oracle instance pulls its own tag on a timer, reads its secrets from OCI Vault into tmpfs and
rolls back to the last healthy image on a failed health check. Setup, secrets and checks:
[docs/runbooks/oracle-setup.md](docs/runbooks/oracle-setup.md).

## Safety modes

Only these modes are valid:

- `SIMULATION`
- `PAPER_TRADING`
- `HISTORICAL_REPLAY`
- `LIVE_MONITORING` (default: public read-only BTC/ETH/SOL telemetry)

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
cd apps/web && npm run typecheck && npm run contract-check && npm run build && npm run bundle-check
```

Passing software gates establishes software/research readiness only. Claims about durable financial
edge require separate out-of-sample evidence produced and recorded through the Validation Engine.
