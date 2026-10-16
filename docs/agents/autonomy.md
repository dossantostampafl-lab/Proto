# Autonomy loop (testnet)

Two signal sources feed one chain. Neither has order authority.

```text
TradingView alert ─┐
                   ├─> fusion + shrink-only reviewer ─> Risk Engine permit ─> OMS ─> executor (Binance testnet)
own strategy ──────┘                                                           └─> ledger ─> reconciliation
```

- **Own strategy** (`services/signals/proto_strategy.py`, `apps/api/app/strategy_surface.py`):
  EMA 5/20 cross on one-minute bars of the live Binance mid for BTC, ETH and SOL against USDT.
  Strength grows with the EMA gap, so a weak cross stays under the fusion threshold (0.6). A
  15-minute cooldown per instrument stops flapping. Stale or missing quotes feed nothing. Each
  signal is journaled as `PROTO_STRATEGY_SIGNAL` and shows on the TradingView desk of the floor.
- **TradingView**: alerts to `POST /v1/signals/tradingview` with the webhook secret. Behind the
  local reverse proxy the source address is the last `X-Forwarded-For` hop; only a loopback peer
  may supply it, so the official TradingView IP allowlist still holds.
- **Closed test** (`apps/api/app/e2e_drill.py`): once after every API start (and on
  `POST /v1/drills/e2e/run` with the operator token) one strong LONG signal for BTC/USDT goes
  through the same chain. It needs the same permit; an order still resting after 45 s is
  cancelled. Steps, verdict and a digest are journaled as `E2E_DRILL_COMPLETED` and served at
  `GET /v1/drills/e2e/latest`.
- **Retired**: the paper autopilot (`/paper/automation/start`) answers 410. It traded outside
  the permit chain.

The image turns this on (`Dockerfile`: `LIVE_MARKET_SOURCE=BINANCE`, `AGENT_PIPELINE_ENABLED`,
`PROTO_STRATEGY_ENABLED`, `E2E_DRILL_ON_START`). Everything stays on the testnet:
`financial_connectivity` and `real_money_execution` are false.
