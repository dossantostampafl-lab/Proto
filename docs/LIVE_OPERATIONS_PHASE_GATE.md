# LIVE_MONITORING Operations Gate

This gate applies only to the standalone public read-only BTC/ETH/SOL monitor.

## Supported staging runtime

Use `docker-compose.live.yml` for the live-only stack. It contains only the migration job, standalone API, PostgreSQL, Redis, Prometheus and Grafana. It does not start the legacy research/simulation web application.

Required environment variables:

- `PROTO_DB_PASSWORD`
- `GRAFANA_ADMIN_PASSWORD`

The one-shot `migrate` service runs `alembic upgrade head` against PostgreSQL before the API starts. Staging sets `LIVE_DATABASE_AUTO_CREATE=false`, so schema drift or a missing migration fails closed instead of being silently repaired by application startup.

The API, Prometheus and Grafana ports bind to loopback by default. Put an authenticated TLS reverse proxy in front of them when remote access is required.

## Required invariants

Every release candidate must preserve:

- public unauthenticated crypto market data only;
- `financial_connectivity=false`;
- `real_money_execution=false`;
- no account, order, deposit, withdrawal, custody or exchange-credential surface;
- durable-before-fanout when persistence is required;
- restart-safe persisted history that cannot satisfy current live readiness by itself;
- live database metadata isolated from legacy simulation persistence.

## Observability

Prometheus loads `infra/monitoring/live-alerts.yml` and scrapes only `/live/metrics/prometheus` from the standalone API. Grafana provisions the `Proto Live Monitoring` dashboard automatically.

Critical alerts cover:

- live metrics scrape loss;
- monitor task stopped;
- stale receipt timestamps;
- required persistence unavailable;
- any non-zero financial-connectivity invariant;
- any non-zero real-money-execution invariant.

Warning alerts cover public-feed disconnects, stale source messages, stale source timestamps, degraded persisted-history reads, history backend failures, requests reaching a process with persistence disabled and repeated parser errors.

The dashboard includes freshness, feed recovery, durability and persisted-history read telemetry.

## Deterministic chaos gate

The automated release suite proves that transient durable-write failures:

1. reject affected ticks before snapshots/history/fanout;
2. recover on a later successful write without process restart;
3. clear old-generation snapshots before accepting data from a new connection generation;
4. do not leak an old snapshot when the first durable write in a new generation fails;
5. distinguish transient history-read failure from write health and recover read health on the next successful query;
6. preserve both financial capability invariants as false.

## Automated backup and restore drill

The release suite creates a local durable live database, writes a public market observation, copies the database as a backup, opens the restored database through a fresh journal and verifies that the observation is queryable.

The same test verifies that restored persisted history does not hydrate the monitor's current snapshot or make current live coverage complete. Historical recovery and live readiness remain separate state domains.

## CI release job

The `CI` workflow contains a dedicated `live-release` job. It must:

1. validate `docker-compose.live.yml` with required staging variables present;
2. run live-only chaos/recovery tests;
3. run the backup/restore drill;
4. run the staging deployment contract tests;
5. run the static live-isolation gate.

The ordinary Python, Rust and web jobs still run independently, so the live release gate is additive rather than a replacement for the repository-wide CI.

## Staging validation

Before promoting a release candidate to a remote environment:

1. Start the live-only stack with fresh volumes and confirm the migration service completes successfully before the API starts.
2. Confirm `/health` returns the two capability invariants as false.
3. Confirm `/live/ready` becomes ready only after fresh BTC, ETH and SOL observations and healthy required persistence.
4. Confirm Prometheus target `proto-live-read-only` is up and all rule files load without error.
5. Confirm the provisioned Grafana dashboard renders freshness, recovery, durability and history-read telemetry.
6. Restart the API and verify persisted history is queryable while readiness remains false until fresh post-restart observations arrive.
7. Restart Redis and verify the event runtime recovers without changing the live financial capability invariants.
8. Restart PostgreSQL and verify durable-before-fanout fails closed while storage is unavailable and recovers when storage returns.
9. Verify there are no prediction, simulation, portfolio, fill or trading-oriented routes in the standalone app.
10. Verify the database contains the Alembic-managed live schema without legacy simulation tables.

## Exit criteria

This phase can merge only when:

- the `live-release` CI job is green;
- repository Python, Rust and web CI jobs are green;
- Security is green;
- Graphify is green on the exact PR head;
- staging contracts and read-only safety invariants remain intact.
