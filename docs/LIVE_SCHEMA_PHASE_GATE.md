# LIVE_MONITORING Schema Management Gate

This gate applies only to the standalone public read-only BTC/ETH/SOL persistence schema.

## Metadata isolation

The live runtime owns `LiveBase.metadata`, which contains only `live_market_ticks`. It must not import the legacy simulation persistence metadata or create simulation tables as a side effect.

## Development and tests

`LIVE_DATABASE_AUTO_CREATE=true` is permitted for local development and isolated tests. In this mode the standalone live metadata may create its own schema directly.

## Staging and release candidates

Staging must set:

- `PERSISTENCE_ENABLED=true`
- `LIVE_DATABASE_AUTO_CREATE=false`

`docker-compose.live.yml` runs the one-shot `migrate` service before the API. The migration command is:

```text
alembic upgrade head
```

The API starts only after the migration service exits successfully. A missing or incompatible schema must fail closed rather than accepting market data without required durable storage.

## Migration contract

The initial revision creates only `live_market_ticks` with:

- a primary key;
- public venue and BTC/ETH/SOL symbol fields;
- connection generation and source sequence;
- source, receipt and persistence timestamps;
- bid, ask, last, volume and public top-of-book sizes;
- unique `(venue, symbol, sequence)` idempotency constraint;
- indexes for venue, symbol, connection generation and timestamps;
- composite `(symbol, received_at)` history-query index.

No fill, portfolio, order, account, credential or prediction-market table is part of the standalone live metadata.

## Upgrade validation

A release candidate must prove on an empty database that:

1. `alembic upgrade head` creates `alembic_version` and `live_market_ticks`;
2. legacy `simulation_fills` is not created;
3. the expected history and timestamp indexes exist;
4. the standalone live durability runtime can start with auto-create disabled after the migration;
5. persistence remains durable-before-fanout.

## Downgrade validation

The migration test must also run `alembic downgrade base` on an isolated database and prove the live table is removed without creating or altering legacy simulation tables.

Downgrade is a controlled recovery operation, not a normal production startup path. Back up durable history before applying destructive downgrades.

## Backup and restore

For PostgreSQL staging, back up the `proto_live` database before schema changes. Restore into a fresh database, run the expected migration level, and verify `/live/history/{symbol}` reads the restored observations. Restored history must never make `/live/ready` pass without fresh post-restart BTC, ETH and SOL observations.

## Safety invariants

Schema management must not introduce:

- exchange or broker credentials;
- account connectivity;
- order routing;
- deposits or withdrawals;
- custody or leverage;
- real-money execution.

The runtime invariants remain:

- `financial_connectivity=false`
- `real_money_execution=false`

## Exit criteria

Merge only when CI, Security and Graphify are green on the exact pull-request head and the live persistence metadata remains isolated from all legacy financial/simulation persistence modules.
