# Risk Engine and Risk Permits

Phase 3 of `docs/plans/2026-10-13-tradingview-agent-trading.md`. The Risk Engine is the only
component that can authorize an order. It is deterministic: no LLM and no I/O. Its decision is a
pure function of the intent, the account state, the controls, the quote and the clock.

```text
agent proposal ─> RiskGovernor.assess ─> RiskEngine.evaluate ─> RiskPermit (HMAC, ≤ 60 s, single use)
                      │ latches lockdowns                              │
                      └ kill switches                    OMS consumes via PermitLedger (Phase 4)
```

## Checks (`services/risk/engine.py`)

Every check runs and is reported in `checks`; a single failure rejects the intent and no permit is
issued.

| Check | Rule |
|---|---|
| `stage_certified` | `PAPER` and `SHADOW` always. `CANARY` only for a scope certified at CANARY (`docs/promotion/gates.md`), sized on at most R$500. `LIVE` never yet |
| `global_kill_switch` / `venue_kill_switch` / `strategy_kill_switch` | Halt everything in scope, including exits |
| `instrument_known`, `venue_grid` | Instrument is in the catalog; quantity and price fit the lot, tick and minimum notional |
| `spot_no_short`, `sell_is_reduce_only` | A sell never exceeds the position and must be reduce-only |
| `fresh_quote` | Quote present, not crossed, and between 0 and `RISK_MAX_QUOTE_AGE_SECONDS` old |
| `price_collar` | A buy limit is ≤ ask + collar; a sell limit is ≥ bid − collar (default 50 bps) |
| `session_open` | 24/7 for crypto; for B3, weekdays 10:00–17:55 America/Sao_Paulo. Holidays are not modelled; the venue rejects orders on those days |
| `no_lockdown` | No latched lockdown, and no current breach of 1% daily loss or 5% drawdown |
| `risk_per_position` | `notional × stop_distance ≤ min(equity, R$10,000) × 0.25%` |
| `gross_leverage` | `exposure + notional ≤ min(equity, R$10,000) × 1.0` |
| `venue_order_notional` | Optional per-venue cap. It can only tighten |

Risk-reducing orders (reduce-only sells within the position) skip the four account-level checks,
so the book can always be de-risked during a lockdown. They never pass a kill switch.

## Permits (`services/risk/permits.py`)

- A permit is HMAC-SHA256 over its id, the intent id, the intent's SHA-256 fingerprint, the maximum
  quantity, and its issue and expiry times. Changing any field or the intent invalidates it.
- The default TTL is 10 s. The contract caps it at 60 s.
- `PermitLedger.consume` verifies the permit and records its id until expiry; a second use returns
  `already_used`. With persistence the OMS spends permits through `SqlPermitLedger`, which keeps
  every spent id in the database, so single use survives restarts.
- The signing key comes from `RISK_PERMIT_SIGNING_KEY`. Production requires at least 32
  characters. When unset, each process uses a random key, so permits never survive a restart.
- Signatures never leave the engine: journal events and API responses carry only the permit id
  and expiry.

## Governor (`services/risk/governor.py`)

- Automation can only tighten the controls.
  - A breach observed during an assessment latches a `DAILY_LOSS` or `DRAWDOWN` lockdown. Recovery
    does not clear it.
  - Kill switches are global, per venue and per strategy. The runtime kill switch
    (`/killswitch/trigger`) also halts the engine.
- Every release is an operator action. A lockdown cannot be released while its breach still holds.

## API

| Route | |
|---|---|
| `GET /v1/risk/status` | Constitution, policy, controls, signing-key mode |
| `GET /v1/risk/decisions/recent` | Latest assessments with every check |
| `POST /v1/risk/killswitch` | Operator. `{"scope": "GLOBAL"\|"VENUE"\|"STRATEGY", "key": ..., "action": "TRIGGER"\|"RELEASE"}` |
| `POST /v1/risk/lockdown/release` | Operator. Returns 409 while still breached |

Every proposal from the agent pipeline is assessed automatically. The journal records
`RISK_DECISION`, `RISK_LOCKDOWN_LATCHED`, `RISK_KILL_SWITCH_CHANGED` and `RISK_LOCKDOWN_RELEASED`.

## Current limitations (closed by later phases)

- Permits are consumed by the paper OMS (`docs/oms/order-management.md`) when
  `OMS_PAPER_EXECUTION_ENABLED=true`; otherwise they expire unused.
- Account state comes from the paper ledger. FX between BRL and USDT is not modelled, so each quote
  currency is risk-managed as its own account.
