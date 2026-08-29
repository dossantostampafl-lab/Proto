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

Frames are normalized into the canonical `MarketTick` contract before they are accepted by the live monitor.

## Data-quality gate

A frame is rejected when the canonical data-quality checks fail. The live readiness endpoint remains fail-closed when there are no fresh accepted frames.

The monitor keeps only a bounded in-memory history per symbol for descriptive measurements such as:

- simple and log return;
- realized volatility over the observed samples;
- average/current spread in basis points;
- current L1 imbalance;
- current microprice;
- observation-span duration.

These measurements are descriptive monitoring outputs. They are not an order or execution interface.

## Operational endpoints

- `GET /live/status` - live monitor state, freshness and public-feed health;
- `GET /live/source-health` - connection attempts, reconnects and current public-feed state;
- `GET /live/ready` - fail-closed readiness check;
- `GET /live/market-data` - accepted public snapshots;
- `GET /live/market-data/{symbol}` - one accepted public snapshot;
- `GET /live/analytics/{symbol}` - bounded descriptive analytics;
- `POST /live/start` - start the read-only monitor;
- `POST /live/stop` - stop the read-only monitor.

WebSocket channels `market-data` and `orderbook` carry only normalized public monitoring payloads when this live monitor is the producer.

## CI and Graphify

Every change remains behind the normal CI, Security and Graphify workflows. The live monitor must remain independently testable without external network access; unit/integration tests inject canonical ticks rather than depending on the public WebSocket.
