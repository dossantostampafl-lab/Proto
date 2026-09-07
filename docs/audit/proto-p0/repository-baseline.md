# PROTO P0 Repository Baseline

Baseline captured on 2026-09-07 UTC before dependency installation or test execution.

## Repository identity

- Root: `/workspace/scratch/85e67c84bacb/proto-repo`
- Remote: `https://github.com/dossantostampafl-lab/Proto.git`
- Default branch: `main`
- Audit branch: `codex/proto-p0-audit`
- Baseline commit: `b4368e259ee4ad8632c5c8a73924ad3661218444`
- Tags: none returned by `git tag --list`
- Submodules: none
- Initial worktree state: clean (`main...origin/main`)
- Audit checkout: dedicated fresh clone; no user modifications were present

## Governing instructions

The only repository instruction file found is `AGENTS.md`. Its effective rules include:

- use Graphify artifacts before broad architecture search when they exist;
- keep architecture, Python, Rust, data, frontend, tests, security, and performance workstreams separate;
- never write to `main`; use feature branches and pull requests;
- never merge with failing CI/security gates;
- prefer deterministic offline fixtures in CI;
- do not commit secrets or generated Graphify output;
- follow `PLAN -> IMPLEMENT -> TEST -> AUDIT -> FIND DEFECTS -> FIX -> RETEST -> RE-AUDIT`.

No `graphify-out/graph.json` or `graphify-out/GRAPH_REPORT.md` exists at the baseline commit, so filesystem discovery is the available fallback.

## Current safety contract

`AGENTS.md`, `README.md`, and `pyproject.toml` define the current product as a research terminal with read-only public crypto monitoring. The repository explicitly prohibits broker/exchange credentials, account access, custody, leverage, withdrawals, and real-money order execution. Valid execution modes in `README.md` are `SIMULATION`, `PAPER_TRADING`, and `HISTORICAL_REPLAY`.

This conflicts with the separately approved autonomous real-money design dated 2026-09-07. P0 remains permissible because it is an observation and verification phase. Any later implementation of financial connectivity requires an explicit repository-governance change before code work begins.

### Post-baseline governance decision

After this baseline was captured, the repository owner explicitly authorized real-money trading development on 2026-09-07. Commit history after the baseline records the resulting contract change in `AGENTS.md` and `docs/adr/autonomous-real-money-execution-boundary.md`. This addendum does not alter the observed state of baseline commit `b4368e259ee4ad8632c5c8a73924ad3661218444`: the current implementation still reports real-money execution as disabled until a scoped path is built and certified.

## Repository inventory

- Total non-Git files at baseline: 428
- `apps`: 94 files
- `services`: 83 files
- `engines`: 8 files
- `tests`: 186 files
- `migrations`: 8 files
- `infra`: 5 files
- `docs`: 20 files
- `.github`: 10 files

Primary stacks and manifests:

- Python API and services: `pyproject.toml`, Python `>=3.13`
- React/Vite frontend: `apps/web/package.json`, `apps/web/package-lock.json`, CI Node 22
- Rust workspace: `Cargo.toml`, `engines/risk-rust/Cargo.toml`, `engines/execution-rust/Cargo.toml`, Rust 1.85
- Persistence: Alembic configuration and six versioned migrations
- Runtime packaging: `Dockerfile`, `Dockerfile.api`, `docker-compose.yml`, `docker-compose.live.yml`, `railway.json`
- Monitoring: Prometheus configuration, Grafana provisioning/dashboard, live alert rules
- CI: Python, mutation, live-release, Rust, web, security, production contract/smoke, deployment fallback, and Graphify workflows

Locking caveats:

- The frontend has `package-lock.json` and CI uses `npm ci`.
- No Python lockfile was found; dependencies are bounded ranges in `pyproject.toml`.
- No Rust `Cargo.lock` was found at the baseline commit.
- Frontend direct dependencies use `latest` in `package.json`; the lockfile is therefore the reproducibility boundary.

## Toolchain baseline

| Tool | Repository/CI requirement | Available locally | Baseline status |
|---|---:|---:|---|
| Python | 3.13+ | 3.12.13 | incompatible |
| Node | 22 in CI | 24.19.0 | available, version differs |
| npm | lockfile-driven | 11.9.0 | available; emitted `http-proxy` config warning |
| Rust | 1.85 | unavailable | blocked |
| Cargo | 1.85 toolchain | unavailable | blocked |
| Docker | required for compose gates | unavailable | blocked |
| Docker Compose | required for live compose gate | unavailable | blocked |

## Authoritative validation commands

From `README.md` and `.github/workflows/ci.yml`:

```bash
python -m pip install -e '.[dev]'
ruff check apps services tests
pytest
cargo fmt --all -- --check
cargo clippy --workspace --all-targets --all-features -- -D warnings
cargo test --workspace --all-features
cd apps/web && npm ci
cd apps/web && npm run typecheck
cd apps/web && npm run contract-check
cd apps/web && npm run build
cd apps/web && npm run bundle-check
docker compose -f docker-compose.live.yml config
```

The baseline phase records missing toolchains before attempting installation or executing these gates.

## Environment-variable names

Only names were inventoried; values were not printed or copied:

`APP_ENV`, `SYSTEM_MODE`, `LIVE_MONITORING_AUTOSTART`, `LIVE_MARKET_SOURCE`, `LIVE_HISTORY_RETENTION_SECONDS`, `LIVE_HISTORY_QUERY_MAX`, `LIVE_HISTORY_PRUNE_EVERY_WRITES`, `LIVE_DATABASE_AUTO_CREATE`, `POSTGRES_DB`, `POSTGRES_USER`, `POSTGRES_PASSWORD`, `DATABASE_URL`, `PERSISTENCE_ENABLED`, `ORCHESTRATION_PERSISTENCE_ENABLED`, `REDIS_URL`, `EVENT_BUS_BACKEND`, `ALPACA_EQUITY_SYMBOLS`, `BRAPI_EQUITY_SYMBOLS`, `CREATION_BRIDGE_SHARED_SECRET`, `HTTP_RATE_LIMIT_PER_MINUTE`, `API_HOST`, `API_PORT`, `WEB_PORT`, `VITE_API_BASE_URL`, `RUST_LOG`, `MINIMUM_NET_EDGE`, `MINIMUM_CONFIDENCE`, `MAX_POSITION`, `MAX_NOTIONAL`, `MAX_DAILY_DRAWDOWN`, `GRAFANA_ADMIN_PASSWORD`.

Names such as `ALPACA_EQUITY_SYMBOLS` are not evidence of authenticated brokerage connectivity. Their implementation and provenance are deferred to the architecture and safety-path tasks.
