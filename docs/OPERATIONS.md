# Proto operations runbook

Use this runbook for local research, simulation, paper trading, and historical replay only.

## Startup gates

1. Confirm the configured mode is `SIMULATION`, `PAPER_TRADING`, or `HISTORICAL_REPLAY`.
2. Start the API and check `GET /health` returns `status: ok`.
3. Check `GET /ready`. A `503` means configured persistence is unavailable.
4. Check `GET /metrics` reports `real_money_execution: false`.
5. Check `GET /v1/reconciliation` reports `consistent: true` before and after a run.

Do not continue if any configuration references broker/exchange credentials, deposits,
withdrawals, custody, leverage, or live order routing. Those capabilities are outside Proto.

## Observability

- `X-Request-ID` is accepted or generated and returned for HTTP correlation.
- `/metrics` exposes HTTP counts, error counts, latency, and simulation/replay counters.
- `/ready` probes the database only when persistence is enabled.
- `/v1/reconciliation` compares the in-memory journal, authoritative fill store, and positions.
- WebSocket clients are bounded per channel; failed or slow peers are pruned independently.

## Incident actions

1. Call `POST /killswitch/trigger` to stop simulation/replay processing.
2. Capture `/health`, `/ready`, `/metrics`, and `/v1/reconciliation` responses.
3. Preserve the fill journal and request IDs; do not rewrite historical records.
4. Diagnose and retest locally.
5. Reset the kill switch only after reconciliation is consistent and all gates are green.

## Chaos and performance checks

- Disconnect a WebSocket peer during broadcast; healthy peers must continue receiving frames.
- Send an oversized WebSocket message; the server must close it with code `1009`.
- Exceed a channel's connection capacity; the server must reject it with code `1013`.
- Attempt an unapproved browser origin; the server must close it with code `1008`.
- Tamper with a reconstructed position in a test; reconciliation must report
  `POSITION_MISMATCH`.
- Replay datasets are capped at 100,000 frames and seek is bounded to the loaded dataset.

## Recovery

For replay, use `POST /replay/reset`, reload the historical dataset, and verify reconciliation.
For simulation, use `POST /simulation/reset`. This clears volatile simulated state; persisted
research records remain governed by the configured journal.

