# LIVE_MONITORING Operations Gate

This gate applies only to the standalone public read-only BTC/ETH/SOL monitor.

## Supported staging runtime

Use `docker-compose.live.yml` for the live-only stack. It contains only the standalone API, PostgreSQL, Redis, Prometheus and Grafana. It does not start the legacy research/simulation web application.

Required environment variables:

- `PROTO_DB_PASSWORD`
- `GRAFANA_ADMIN_PASSWORD`

The API, Prometheus and Grafana ports bind to loopback by default. Put an authenticated TLS reverse proxy in front of them when remote access is required.

## Required invariants

Every release candidate must preserve:

- public unauthenticated crypto market data only;
- `financial_connectivity=false`;
- `real_money_execution=false`;
- no account, order, deposit, withdrawal, custody or credential surface;
- durable-before-fanout when persistence is required;
- restart-safe persisted history that cannot satisfy current live readiness by itself.

## Observability

Prometheus loads `infra/monitoring/live-alerts.yml` and scrapes only `/live/metrics/prometheus` from the standalone API. Grafana provisions the `Proto Live Monitoring` dashboard automatically.

Critical alerts cover:

- live metrics scrape loss;
- monitor task stopped;
- stale receipt timestamps;
- required persistence unavailable;
- any non-zero financial-connectivity invariant;
- any non-zero real-money-execution invariant.

Warning alerts cover public-feed disconnects, stale source messages, stale source timestamps, degraded persisted-history reads and repeated parser errors.

## Deterministic chaos gate

The unit suite must prove that repeated transient durable-write failures:

1. reject affected ticks before snapshots/history/fanout;
2. do not corrupt sequence progression;
3. recover on later successful writes without process restart;
4. keep the in-memory analytics window bounded;
5. preserve both financial capability invariants as false.

## Staging validation

Before promoting a release candidate:

1. Start the live-only stack with fresh volumes.
2. Confirm `/health` returns the two capability invariants as false.
3. Confirm `/live/ready` becomes ready only after fresh BTC, ETH and SOL observations and healthy required persistence.
4. Confirm Prometheus target `proto-live-read-only` is up and all rule files load without error.
5. Confirm the provisioned Grafana dashboard renders freshness and durability telemetry.
6. Restart the API and verify persisted history is queryable while readiness remains false until fresh post-restart observations arrive.
7. Restart Redis and verify the application recovers after the event runtime reconnects/restarts.
8. Restart PostgreSQL and verify durable-before-fanout fails closed while storage is unavailable and recovers when storage returns.
9. Verify there are no `/probability`, `/edge`, `/simulation`, `/replay`, `/portfolio`, `/fills` or trading-oriented WebSocket routes in the standalone app.

## Backup and restore drill

The PostgreSQL volume is the durable source for live history. A staging release is not considered operationally complete until a backup can be restored into a fresh database and `/live/history/{symbol}` returns the restored read-only observations. Restored history must never make `/live/ready` pass without current live data.

## Exit criteria

This phase can merge only when CI, Security and Graphify are green on the exact PR head and the standalone deployment remains read-only by construction.
