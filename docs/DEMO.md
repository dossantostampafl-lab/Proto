# Five-minute simulation demo

This demo uses synthetic historical frames and never sends a live order.

## 1. Verify the boundary

```bash
curl http://localhost:8000/health
curl http://localhost:8000/ready
curl http://localhost:8000/risk
```

Confirm the mode is simulation-only and `real_money_execution` is `false`.

## 2. Load historical replay

```bash
curl -X POST http://localhost:8000/replay/start \
  -H 'Content-Type: application/json' \
  -d '{"speed":"5x","frames":[
    {"timestamp":"2026-01-01T00:00:00Z","snapshot":{"symbol":"BTC","market_id":"btc-demo","bid":60000,"ask":60010}},
    {"timestamp":"2026-01-01T00:00:01Z","snapshot":{"symbol":"BTC","market_id":"btc-demo","bid":60005,"ask":60015}},
    {"timestamp":"2026-01-01T00:00:02Z","snapshot":{"symbol":"BTC","market_id":"btc-demo","bid":60020,"ask":60030}}
  ]}'
```

The dashboard will show the replay state and streamed market/order-book frames.

## 3. Exercise complete controls

```bash
curl -X POST http://localhost:8000/replay/pause
curl -X POST http://localhost:8000/replay/seek -H 'Content-Type: application/json' -d '{"cursor":1}'
curl -X POST http://localhost:8000/replay/speed -H 'Content-Type: application/json' -d '{"speed":"100x"}'
curl -X POST http://localhost:8000/replay/step
curl -X POST http://localhost:8000/replay/resume
curl -X POST http://localhost:8000/replay/restart
```

The same actions are available in the dashboard.

## 4. Verify integrity and observability

```bash
curl http://localhost:8000/metrics
curl http://localhost:8000/v1/reconciliation
```

Reconciliation must report `consistent: true`.

## 5. Demonstrate the stop boundary

```bash
curl -X POST http://localhost:8000/killswitch/trigger
curl http://localhost:8000/risk
curl -X POST http://localhost:8000/replay/reset
```

The runtime stops processing and remains inside the simulation/replay boundary.

