# Five-minute live data read-only demo

This demo receives public book tickers and never authenticates or sends an order.

## 1. Verify the boundary

```bash
curl http://localhost:8000/health
curl http://localhost:8000/ready
curl http://localhost:8000/risk
```

Confirm the mode is `LIVE_DATA_READ_ONLY`, `live_data_read_only` is `true`, and
`real_money_execution` is `false`.

## 2. Start the public feed

```bash
curl -X POST http://localhost:8000/live/start \
  -H 'Content-Type: application/json' \
  -d '{"source":"binance","symbol":"BTCUSDT"}'
curl http://localhost:8000/live/status
```

The dashboard shows the read-only badge, connection state, bid/ask frames, latency, staleness,
rejections and reconnects. Repeat with `ETHUSDT` or `SOLUSDT` if desired.

## 3. Verify readiness and observability

```bash
curl http://localhost:8000/ready
curl http://localhost:8000/metrics
```

After the first tick, readiness must be healthy and `live_data.state` must be `STREAMING`.

## 4. Demonstrate the security boundary

```bash
curl -X POST http://localhost:8000/live/start \
  -H 'Content-Type: application/json' \
  -d '{"source":"http://169.254.169.254/latest/meta-data","symbol":"BTCUSDT"}'
curl -X POST http://localhost:8000/v1/portfolio/mark \
  -H 'Content-Type: application/json' \
  -d '{"marks":[{"asset":"BTC","price":60000}]}'
```

The untrusted source is rejected, and portfolio mutation returns `409` while live mode is active.

## 5. Demonstrate the stop boundary

```bash
curl -X POST http://localhost:8000/killswitch/trigger
curl http://localhost:8000/risk
curl http://localhost:8000/live/status
```

The live feed stops and the runtime remains incapable of real-money execution.

