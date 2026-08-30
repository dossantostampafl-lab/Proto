# Proto operations runbook

Use this runbook for public live market-data observation, local research, simulation, paper
trading, and historical replay. Live data remains strictly read-only.

## Startup gates

1. Confirm the configured mode is `LIVE_DATA_READ_ONLY`.
2. Start the API and check `GET /health` returns `status: ok`.
3. Check `GET /ready`. A `503` means persistence is unavailable or a requested live feed is
   disconnected/stale.
4. Check `GET /metrics` reports `real_money_execution: false`.
5. Check `GET /v1/reconciliation` reports `consistent: true` before and after a run.

Startup fails closed if the environment contains private exchange, broker, wallet, trading, or
order-routing credentials. Do not add deposits, withdrawals, custody, leverage, authenticated
account channels, or live order routing.

## Start and stop live data

```bash
curl -X POST http://localhost:8000/live/start \
  -H 'Content-Type: application/json' \
  -d '{"source":"binance","symbol":"BTCUSDT"}'
curl http://localhost:8000/live/status
curl -X POST http://localhost:8000/live/stop
```

Only `binance` and the symbols `BTCUSDT`, `ETHUSDT`, and `SOLUSDT` are accepted. The outbound
connection is fixed to the unauthenticated public TLS feed; callers cannot supply a URL.

## Observability

- `X-Request-ID` is accepted or generated and returned for HTTP correlation.
- `/metrics` exposes HTTP counts, errors, latency, live ticks/rejections/reconnects/gaps, and
  simulation/replay counters.
- `/metrics/prometheus` exposes the same operational counters in Prometheus text format.
- `/live/status` exposes source, symbol, feed state, last tick, latency, staleness, rejected
  frames, sequence gaps, reconnects, and the read-only flag.
- `/ready` probes the database and degrades when an active live feed is stale or disconnected.
- `/v1/reconciliation` compares the in-memory journal, authoritative fill store, and positions.
- Reconciliation also runs periodically and broadcasts its result on the risk channel.
- WebSocket clients are bounded per channel; failed or slow peers are pruned independently.

## Incident actions

1. Call `POST /killswitch/trigger` to stop live ingestion, simulation, or replay processing.
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

For live data, call `POST /live/stop`, inspect `last_error`, network/DNS/TLS reachability and
provider status, then call `POST /live/start` again. Never work around failure by adding private
credentials or a caller-provided endpoint.

