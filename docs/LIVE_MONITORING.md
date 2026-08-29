# Live Monitoring

`LIVE_MONITORING` is the read-only runtime for public BTC, ETH and SOL market data.

## Boundary

This mode is intentionally account-free and execution-free:

- public unauthenticated market-data input only;
- no exchange account credentials;
- no order submission or order-management API;
- no deposits, withdrawals, custody or leverage;
- `financial_connectivity=false` is invariant;
- `real_money_execution=false` is invariant.

The live path is for observation, data quality, descriptive analytics, WebSocket delivery and operational monitoring.

## Public source

The current adapter consumes the public Coinbase market-data WebSocket for:

- `BTC-USD` -> `BTC`;
- `ETH-USD` -> `ETH`;
- `SOL-USD` -> `SOL`.

Transport and parsing are isolated. `services/market_data/live.py` owns WSS connection health and reconnect behavior, while `services/market_data/public_feed_parser.py` owns public-frame validation and normalization. Frames are normalized into the canonical `MarketTick` contract before they are accepted by the live monitor.

The parser rejects malformed JSON, invalid or negative sequences, timezone-naive timestamps and unsupported products, and normalizes offset-aware timestamps to UTC. Wire payloads are capped at 256 KiB, ticker frames at 32 events and each event at 32 ticker entries. The WebSocket transport uses the same 256 KiB maximum message size so oversized frames are bounded before application parsing.

## Data-quality gate

A frame is rejected when the canonical data-quality checks fail. Checks include:

- stale data;
- excessive future clock skew;
- timezone-naive timestamps;
- non-finite numeric values such as `NaN` or infinity;
- non-positive prices;
- invalid spreads;
- negative sizes or volume;
- duplicate/out-of-order sequences;
- out-of-order timestamps;
- excessive one-frame price jumps;
- symbols outside the configured BTC/ETH/SOL allowlist.

Rejected frames do not enter the accepted snapshot, rolling analytics history or WebSocket output. Data-quality thresholds themselves reject non-finite configuration so `NaN` or infinity cannot silently disable a gate.

The monitor keeps only a bounded in-memory history per symbol for descriptive measurements such as:

- simple and log return;
- realized volatility over the observed samples;
- average/current spread in basis points;
- current L1 imbalance;
- current microprice;
- observation-span duration.

These measurements are descriptive monitoring outputs. They are not an order or execution interface.

## Connection health and reconnect isolation

The public adapter exposes operational telemetry without account connectivity:

- connection attempts and reconnect count;
- connection generation;
- frames received and normalized ticks emitted;
- parser error count and consecutive parser degradation;
- message-timeout count;
- current connection start time;
- last message and last tick receive times;
- last transport/parser error type.

A successful reconnect increments the connection generation. When the monitor observes a new generation it resets the data-quality sequence state and clears accepted snapshots, rolling analytics history and per-symbol generation state before accepting data from the new connection. This prevents cached observations or sequence numbers from an old socket being treated as current.

The public WebSocket receive loop is timeout-bounded. A socket that remains open but stops delivering messages is closed by the client path and reconnected with bounded exponential backoff. Backoff is reset only after valid ticker data is observed, rather than merely after a TCP/WebSocket connection succeeds. Explicit async-generator shutdown also clears the connected-state telemetry in a `finally` path.

Malformed public frames use a small consecutive-error budget. Isolated parser failures are counted without immediately tearing down a healthy socket, but repeated failures cross the budget and force a reconnect. Heartbeat/control frames do not clear parser degradation; only valid ticker data does.

Readiness requires BTC, ETH and SOL to have fresh observations from the current connection generation. The source heartbeat/message age is checked independently, so a connected socket without recent messages remains not ready.

`GET /live/ready` is fail-closed and reports deterministic `readiness_failures`, including conditions such as:

- `MONITOR_STOPPED`;
- `SOURCE_DISCONNECTED`;
- `SOURCE_MESSAGES_STALE`;
- `SOURCE_PARSE_DEGRADED`;
- `NO_FRESH_DATA`;
- `INCOMPLETE_SYMBOL_COVERAGE`;
- `STALE_SYMBOL_COVERAGE`;
- `CURRENT_CONNECTION_INCOMPLETE`.

## Operational endpoints

The HTTP monitoring surface is intentionally read-only. Starting and stopping the monitor is an internal lifecycle concern controlled by `LIVE_MONITORING_AUTOSTART`, not a public HTTP operation.

- `GET /live/status` - live monitor state, freshness, per-symbol connection generation and public-feed health;
- `GET /live/source-health` - transport counters, reconnect generation, heartbeat/message age and current public-feed state;
- `GET /live/ready` - fail-closed readiness check with explicit failure reasons;
- `GET /live/market-data` - accepted public snapshots;
- `GET /live/market-data/{symbol}` - one accepted public snapshot;
- `GET /live/analytics/{symbol}` - bounded descriptive analytics.

WebSocket channels `market-data` and `orderbook` carry only normalized public monitoring payloads when this live monitor is the producer. Fanout to the two channels is concurrent, failed peers are pruned, send operations are timeout-bounded, origins are checked, message sizes are bounded and per-channel connection capacity is enforced with an async lock to prevent concurrent admission races.

## CI, Security and Graphify

Every change remains behind the normal CI, Security and Graphify workflows. Graphify is used to inspect cross-module coupling and guide safe refactors. The public live monitor depends on the `PublicMarketDataAdapter` protocol rather than a concrete transport implementation, public parsing is isolated from the transport adapter, and live coverage/readiness evaluation is split into pure functions for deterministic testing.

The Security workflow keeps Python, Web and Rust dependency audits independent. The Rust scanner is version-pinned for reproducible runs while the active dependency graph is checked before the single documented lockfile-only `rkyv` advisory exception is applied.

The live monitor must remain independently testable without external network access. Unit and integration tests inject canonical ticks or a read-only adapter stub rather than depending on the public WebSocket.
