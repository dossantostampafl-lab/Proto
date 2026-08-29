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

Frames are normalized into the canonical `MarketTick` contract before they are accepted by the live monitor. The parser rejects malformed JSON, invalid sequences, timezone-naive timestamps and unsupported products.

## Data-quality gate

A frame is rejected when the canonical data-quality checks fail. Checks include stale data, excessive future clock skew, timezone-naive timestamps, invalid spreads, negative sizes or volume, duplicate/out-of-order sequences, out-of-order timestamps and excessive one-frame price jumps.

The live readiness endpoint remains fail-closed when there are no fresh accepted frames.

The monitor keeps only a bounded in-memory history per symbol for descriptive measurements such as:

- simple and log return;
- realized volatility over the observed samples;
- average/current spread in basis points;
- current L1 imbalance;
- current microprice;
- observation-span duration.

These measurements are descriptive monitoring outputs. They are not an order or execution interface.

## Connection health

The public adapter exposes operational telemetry without account connectivity:

- connection attempts and reconnect count;
- connection generation;
- frames received and normalized ticks emitted;
- parser error count;
- current connection start time;
- last message and last tick receive times;
- last transport/parser error type.

A reconnect increments the connection generation. The monitor associates every accepted symbol with the generation that produced it. Readiness requires BTC, ETH and SOL to have fresh observations from the current connection generation, which prevents recently cached pre-reconnect observations from being mistaken for a healthy new connection.

The source heartbeat/message age is also checked. A connected socket without recent messages does not become ready.

## Operational endpoints

- `GET /live/status` - live monitor state, freshness, per-symbol connection generation and public-feed health;
- `GET /live/source-health` - transport counters, reconnect generation, heartbeat/message age and current public-feed state;
- `GET /live/ready` - fail-closed readiness check;
- `GET /live/market-data` - accepted public snapshots;
- `GET /live/market-data/{symbol}` - one accepted public snapshot;
- `GET /live/analytics/{symbol}` - bounded descriptive analytics;
- `POST /live/start` - start the read-only monitor;
- `POST /live/stop` - stop the read-only monitor.

WebSocket channels `market-data` and `orderbook` carry only normalized public monitoring payloads when this live monitor is the producer.

## CI and Graphify

Every change remains behind the normal CI, Security and Graphify workflows. Graphify is used to inspect cross-module coupling and guide safe refactors; the public live monitor depends on the `PublicMarketDataAdapter` protocol rather than a concrete transport implementation.

The live monitor must remain independently testable without external network access. Unit and integration tests inject canonical ticks or a read-only adapter stub rather than depending on the public WebSocket.
