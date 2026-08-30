# LIVE_MONITORING History API Gate

The persisted history API is a read-only observability surface for public BTC/ETH/SOL market observations.

## Endpoint

`GET /live/history/{symbol}` supports:

- `limit` with the configured hard maximum;
- opaque `cursor` pagination;
- optional timezone-aware `start_at` and `end_at` receipt-time bounds.

Results remain ordered newest-first by `(received_at, id)` and return `next_cursor` plus `has_more`.

## Cursor contract

The cursor is opaque to API clients. It is bounded to 512 characters and versioned internally. Version 2 binds the continuation position to the requested symbol and normalized time window, so a cursor from one symbol or time range cannot be reused for another query scope.

Invalid, malformed, unsupported or query-mismatched cursors return HTTP 422. Cursor rejection is treated as a client/query error and must not mark the persistence backend unhealthy.

Pagination must not duplicate or skip rows when the underlying page is traversed without concurrent deletion of retained rows.

## Time bounds

`start_at` and `end_at` must include timezone information. `start_at > end_at` is rejected. Bounds apply to the server receipt timestamp, not an exchange account clock or trading state.

## Retention interaction

History remains subject to the configured retention policy. A cursor pointing behind already-pruned data may yield an empty page; it must never bypass retention or recover deleted observations.

## Read observability

`/live/status` and `/live/metrics/prometheus` expose process-local counters for the persisted history surface:

- requests;
- successful pages;
- rows returned;
- pages with another cursor;
- cursor rejections;
- backend read failures;
- requests received while persistence is disabled.

Client cursor errors and backend failures are counted separately. All numeric Prometheus output continues through the finite-value guard.

## Safety invariants

Every history response remains:

- `source=PUBLIC_READ_ONLY_PERSISTED`;
- `financial_connectivity=false`;
- `real_money_execution=false`.

The endpoint does not expose account data, credentials, orders, positions, fills, deposits, withdrawals, custody or leverage.

## Exit criteria

- SQL tests prove cursor traversal without duplicates or gaps.
- SQL tests prove bounded timezone-aware windows.
- cursors are bound to symbol and time window.
- malformed and query-mismatched cursors fail closed.
- HTTP tests prove pagination metadata and validation.
- observability tests distinguish client cursor rejection from backend failure.
- live isolation gates include the history-observability module.
- CI, Security and Graphify are green on the exact PR head.
