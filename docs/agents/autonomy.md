# Autonomy loop (testnet)

Two signal sources feed one chain. Neither has order authority.

```text
TradingView alert ─┐
                   ├─> fusion + shrink-only reviewer ─> Risk Engine permit ─> OMS ─> executor (Binance testnet)
own strategy ──────┘                                                           └─> ledger ─> reconciliation
```

- **Own strategy** (`services/signals/proto_strategy.py`, `apps/api/app/strategy_surface.py`):
  EMA 5/20 cross on one-minute bars of the live Binance mid for BTC, ETH and SOL against USDT.
  A cross arms the instrument; the signal comes on the first closed bar (within 10) where the EMA
  gap has opened enough: strength = gap / 0.1% of price, at least 0.6 for an entry and 0.2 for an
  exit. At the cross bar the gap is near zero, which is why signals emitted there never traded. A
  cross that reverses or never opens up emits nothing. A 15-minute cooldown per instrument stops flapping. Stale or missing quotes feed nothing. Each
  signal is journaled as `PROTO_STRATEGY_SIGNAL` and shows on the TradingView desk of the floor.
- **TradingView**: alerts to `POST /v1/signals/tradingview` with the webhook secret, for
  `BINANCE_SPOT:BTCUSDT`, `ETHUSDT` and `SOLUSDT` (the pairs with live quotes). Behind the
  local reverse proxy the source address is the last `X-Forwarded-For` hop; only a loopback peer
  may supply it, so the official TradingView IP allowlist still holds.
- **Closed test** (`apps/api/app/e2e_drill.py`): once after every API start (and on
  `POST /v1/drills/e2e/run` with the operator token) one strong LONG signal for BTC/USDT goes
  through the same chain. It needs the same permit; an order still resting after 45 s is
  cancelled. Steps, verdict and a digest are journaled as `E2E_DRILL_COMPLETED` and served at
  `GET /v1/drills/e2e/latest`.
  - **Round trip.** Whatever the entry filled (fully or partially) is sold again by a
    reduce-only IOC SELL at the bid (`services.agents.build_exit`), which goes through the same
    chain: Risk Engine permit, then the OMS and executor; the drill never calls the venue. The
    exit is capped by the entry's fill, by the own position's growth over the entry (a fee
    taken in BTC is not sold) and by the own position minus every agent book, so it never
    sells the agents' coins nor the opening (faucet) holdings; with neither cap known it sells
    nothing. A resting exit is cancelled after the wait like the entry. Steps `exit_permit`,
    `exit_order` and `flat` join the report; the verdict is `PASSED` only when the exit filled
    the planned quantity and reconciliation is consistent. An entry that filled nothing is
    already `flat`.
  - **Orphan cleanup.** Before the entry, the own position per coin (venue balance minus the
    opening holdings) minus the sum of the ACTIVE and ELIMINATED agents' book positions in that
    coin is inventory earlier drills left behind. It is floored to the lot and, when it clears
    the minimum notional, sold through the same permitted chain, journaled as
    `DRILL_INVENTORY_UNWOUND` with the quantity (step `inventory_cleanup`). Without attribution
    data (no roster store, no agents, fills that cannot be synced, a held book on an unknown
    instrument) the cleanup is skipped, never guessed. A refused unwind fails the drill before
    any new entry. Once the inventory is gone the step finds nothing, so it runs before every
    entry at no cost.
- **Retired**: the paper autopilot (`/paper/automation/start`) answers 410. It traded outside
  the permit chain.

The image turns this on (`Dockerfile`: `LIVE_MARKET_SOURCE=BINANCE`, `AGENT_PIPELINE_ENABLED`,
`PROTO_STRATEGY_ENABLED`, `E2E_DRILL_ON_START`). Everything stays on the testnet:
`financial_connectivity` and `real_money_execution` are false.
