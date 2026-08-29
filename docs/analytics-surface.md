# Analytics Surface and Deterministic Demo

## Safety boundary

Proto is restricted to `SIMULATION`, `PAPER_TRADING`, and `HISTORICAL_REPLAY`.

The deterministic BTC/ETH/SOL surfaces in this document are explicitly synthetic. They are not live quotes and are not suitable for real-money execution.

## Canonical analytics endpoints

### Market and microstructure

- `GET /markets/{market_id}`
- `GET /market-data/{symbol}`
- `GET /orderbook/{symbol}`
- `GET /data-quality/{symbol}`

Synthetic demo market IDs:

- `btc-threshold`
- `eth-threshold`
- `sol-threshold`

Every demo response carries a `source` field identifying it as synthetic research data.

### Model research

- `GET /models`
- `GET /models/metrics`
- `GET /models/calibration`
- `GET /probability/{market_id}`
- `GET /edge/{market_id}`
- `GET /expected-value/{market_id}`
- `GET /analytics/greeks/{market_id}`
- `GET /hawkes/{symbol}`
- `POST /research/calibration`

Calibration responses include Brier score, log loss, expected calibration error, and populated reliability-curve buckets. `/models/calibration` intentionally returns `NOT_COMPUTED` until labeled observations are supplied; the API never fabricates model-quality metrics.

Synthetic Greeks are local sensitivities of `baseline-logit-v0`, not exchange Greeks. The API reports the derivative definitions explicitly.

### Lifecycle and expiry analytics

- `GET /market-lifecycle`
- `GET /resolution-grid`
- `GET /analytics/expiry-map`

Resolution state is `PENDING` for the synthetic demo. Expiry horizons are research fixtures and are labeled as synthetic.

### Simulation portfolio

- `GET /portfolio`
- `GET /positions`
- `GET /pnl`
- `GET /v1/portfolio`
- `GET /v1/fills`

The canonical aliases read the same in-process simulation portfolio used by the execution simulator.

### Reconciliation safety

- `GET /v1/reconciliation`
- `POST /v1/reconciliation/enforce`

The enforcement endpoint maps any reconciliation divergence to `HALT`, stops the simulation runtime, triggers the kill-switch state, and broadcasts the updated risk/runtime state.

## Observability

JSON runtime metrics remain available at:

- `GET /metrics`
- `GET /research/metrics`

Prometheus exposition is available at:

- `GET /metrics/prometheus`

Docker Compose includes Redis, Prometheus, and Grafana. Prometheus scrapes the API every five seconds using `infra/monitoring/prometheus.yml`.

## Persistence schema

The database bootstrap initializes the canonical research tables:

`markets`, `market_ticks`, `orderbook_snapshots`, `trades`, `prediction_contracts`, `model_predictions`, `fair_values`, `edges`, `signals`, `risk_decisions`, `orders`, `fills`, `positions`, `hedges`, `portfolio_snapshots`, `pnl_snapshots`, `model_metrics`, `calibration_metrics`, `hawkes_states`, `replay_sessions`, `system_events`, and `audit_events`.

The existing `simulation_fills` table remains the typed fill journal used by the current paper simulator while domain-specific repositories are progressively moved onto the canonical schema.

## Validation gates

Pull requests run the normal Python/Rust/Web CI plus dependency-security audits for Python, Rust, and npm. Chaos tests exercise event retry/dead-letter isolation, idempotency, hash-chain tamper detection, and reconciliation divergence. Local performance tests protect microstructure and probability/edge research paths with deterministic p95 budgets.
