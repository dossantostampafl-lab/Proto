# Standalone LIVE_MONITORING Release Candidate Gate

This is the final repository-level release gate for the public read-only BTC/ETH/SOL monitoring service.

## Artifact boundary

The release artifact is built from `Dockerfile.api` and starts only `apps.api.app.live_app:app`.

The image:

- runs as `proto:proto`, not root;
- is verified while running with a read-only root filesystem, dropped Linux capabilities and `no-new-privileges`;
- installs only `requirements-live.txt` instead of the full research dependency stack;
- uses `.dockerignore` to remove development, frontend and engine assets from the Docker build context;
- exposes no financial account or real-money execution capability.

## Runtime smoke gate

CI builds the actual image and starts it with live ingestion disabled so the smoke gate is deterministic and does not depend on an external market-data connection.

The smoke verifier requires:

- `/health` becomes available;
- `mode=LIVE_MONITORING`;
- `financial_connectivity=false`;
- `real_money_execution=false`;
- security middleware emits `Cache-Control: no-store`;
- a mutating HTTP request to the live API is rejected with HTTP 405.

## API surface gate

The standalone FastAPI OpenAPI schema is audited in tests. HTTP operations may only use read methods. Legacy prediction, simulation, portfolio, fill and financial-account path segments are forbidden.

The only WebSocket paths allowed by the standalone application are:

- `/ws/market-data`;
- `/ws/orderbook`.

Both are observation-only public market-data channels.

## Dependency gate

`requirements-live.txt` contains the runtime packages required for the standalone service and database/event infrastructure. Research packages such as NumPy, pandas, Polars, SciPy, scikit-learn and statsmodels are forbidden from the live image manifest.

Security CI audits both the repository Python environment and the standalone live dependency manifest with `pip-audit`.

## Required CI gates

A release candidate can merge only when the exact PR head has:

- repository Python CI green;
- standalone `live-release` gate green;
- hardened `live-image` build and smoke gate green;
- Rust CI green;
- web build green;
- Security green, including the live dependency audit;
- Graphify green.

## Safety invariants

The release candidate must preserve:

- public unauthenticated BTC/ETH/SOL market observations only;
- no account connectivity or exchange credentials;
- no order routing, deposits, withdrawals, custody or leverage;
- `financial_connectivity=false`;
- `real_money_execution=false`.

Repository-level release readiness does not itself deploy the service to an external host. Remote staging or production deployment requires an explicitly available target environment and its deployment credentials outside the application code.
