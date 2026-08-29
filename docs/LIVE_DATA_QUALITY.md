# LIVE_DATA_READ_ONLY quality gates

This plan covers public market-data ingestion only. It does not authorize order
routing, authenticated exchange APIs, account data, deposits, withdrawals,
transfers, leverage, or any other real-money capability.

## Release gates

| Area | Automated gate | Acceptance criterion |
| --- | --- | --- |
| API contract | `test_live_data_contract.py` | `status/start/stop` expose the documented state and always return `read_only=true` |
| Input security | SSRF and credential-shaped source attacks | Only an allowlisted public source and validated symbol are accepted |
| Execution boundary | OpenAPI and source capability scan | No live trade/order/funds endpoint or private exchange client call exists |
| Data quality | stale, duplicate, and out-of-order tick tests | Invalid ticks are rejected and do not advance the accepted cursor |
| Backpressure | stalled WebSocket peer attack | A slow peer is pruned within the send deadline without delaying healthy peers |
| Fanout | 128-peer bounded fanout | One frame reaches every healthy peer without exceeding the channel cap |
| Hot path | 50,000 sequential quality checks | Linear processing, bounded per-symbol state, under five seconds in CI |

## Required operational signals

`GET /live/status` must expose the mode, connection state, source, symbol,
last accepted timestamp and sequence, received/rejected counters, reconnect
attempts, last error, stale flag, latency, staleness, and the immutable
`read_only=true` proof.

Connection state is one of `STOPPED`, `CONNECTING`, `STREAMING`, or `BACKOFF`.
Reconnect delay must be bounded exponential backoff with jitter. A disconnect,
malformed payload, sequence regression, buffer overflow, or stale stream must be
observable; it must never silently mutate portfolio, fills, orders, or account
state.

## Chaos campaign before a tagged release

Run at least 30 minutes per public adapter while injecting DNS failure, refused
connections, clean and unclean WebSocket closes, malformed JSON, duplicated and
reordered frames, 10-second consumer stalls, and a 10x burst over expected peak.
Record reconnect delay, rejected/received counts, process memory, event-loop lag,
and p50/p95/p99 ingest-to-broadcast latency. The release fails on an unbounded
queue, monotonic memory growth, hidden data loss, credential request, private API
request, or any live execution surface.
