# LIVE_MONITORING Durability & Recovery Phase Gate

## Scope

This phase adds durable storage and restart-safe historical recovery for the public read-only BTC/ETH/SOL monitor.

It does not add financial account connectivity, credentials, order routing, custody, deposits, withdrawals or real-money execution.

## Required invariants

- `financial_connectivity=false` everywhere on the live surface.
- `real_money_execution=false` everywhere on the live surface.
- Only accepted public market-data ticks may be persisted.
- When persistence is enabled, a tick must be durably committed or confirmed idempotently present before it may enter the accepted in-memory snapshot/history or WebSocket fanout.
- A persistence write failure must reject that live frame and make required persistence unhealthy.
- Persisted history must never make live readiness green after a restart.
- Current live snapshots, receive timestamps, source sequence state and connection-generation state must still be rebuilt only from the current public WebSocket connection.

## Durability contract

The SQL journal stores:

- source venue and symbol;
- source timestamp;
- server receipt timestamp;
- persistence timestamp;
- connection generation;
- source sequence;
- bid, ask, last, volume and L1 sizes.

The identity key is `(venue, symbol, sequence)`. Repeated inserts of the same accepted observation are treated as idempotent hits instead of duplicate durable rows.

## Recovery contract

`GET /live/history/{symbol}` exposes bounded persisted history after process restart. The endpoint is read-only and uses `Cache-Control: no-store, max-age=0`.

Durable history recovery is intentionally separate from current live state:

- historical rows remain queryable after restart;
- `snapshot(symbol)` remains empty until a current live tick is accepted;
- live readiness still requires fresh BTC/ETH/SOL receipts from the current connection generation.

## Retention

Retention is bounded by `LIVE_HISTORY_RETENTION_SECONDS` and maintenance is triggered at startup plus periodically after `LIVE_HISTORY_PRUNE_EVERY_WRITES` durable inserts.

Read queries are capped by `LIVE_HISTORY_QUERY_MAX`.

## Observability

`/live/status` and `/live/metrics/prometheus` expose:

- whether persistence is configured and required;
- durable write health and history read health;
- inserted rows and idempotent hits;
- current-connection persistence failures;
- journal read/write/maintenance failure counters;
- pruned row count and retention horizon.

When persistence is required and durable writes are unhealthy, `/live/ready` must include `PERSISTENCE_UNAVAILABLE`.

## Exit criteria

The phase is complete only when all of the following are true:

- SQL journal idempotency tests pass;
- restart-history recovery tests pass;
- retention tests pass;
- durable-before-fanout tests pass;
- required-persistence fail-closed tests pass;
- live isolation tests include all durability modules;
- HTTP history surface remains read-only and non-cacheable;
- CI is green;
- Security is green;
- Graphify is green;
- the phase PR is mergeable and merged to `main` only after those gates are green.
