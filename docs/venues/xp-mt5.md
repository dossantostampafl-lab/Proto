# XP (B3) through MetaTrader 5

XP does not offer an order API to retail accounts; it offers the MetaTrader 5 terminal for B3.
PROTO reaches it through a small bridge on a Windows VM (see `docs/adr/venues-and-executor-hosting.md`).

```text
executor (private subnet) ──bearer token──> MT5 bridge (Windows VM, private subnet) ──> MT5 terminal ──> XP / B3
```

## Pieces

| Piece | Where | What |
|---|---|---|
| `apps/mt5_bridge/service.py` | Windows VM | Private HTTP API over the terminal: health, symbols, quotes, balances, orders |
| `apps/mt5_bridge/gateway.py` | Windows VM | The only code that imports `MetaTrader5`; tests use a fake |
| `services/venues/xp_mt5.py` | executor | `XpMt5Adapter`, the venue adapter the OMS uses (same contract as Binance) |

## Safety

- **Demo only.** The bridge reads the logged-in account before every order or cancel and refuses
  anything but a DEMO account with 403. The adapter maps DEMO to TESTNET and any other account to
  LIVE, which the OMS refuses (`real_money_execution=true`) until a live scope is certified.
- **Credentials** (XP demo login, password, server and the bridge token) live in the Vault and
  only on the Windows VM and the executor. The API never talks to the bridge.
- **Identity.** The OMS client order id maps to one MT5 ticket in the bridge's SQLite store, so a
  resubmit returns the existing order instead of placing another.
- **Unknown outcomes.** A timeout, a dropped connection or a 5xx from the bridge raises, and the
  OMS marks the order UNKNOWN instead of resending it.
- **Grids** (tick size, lot step, minimum volume) come from the terminal's symbol specs. B3 lots
  of 100 make R$10,000 at 0.25% risk too small for most stocks, so PROTO trades the fractional
  market (`PETR4F`, `VALE3F`, `BOVA11F`, lot 1).

## Bridge API (bearer token on everything but `/health`)

| Method | Path | Notes |
|---|---|---|
| GET | `/health` | `connected`, `account_mode` (DEMO, CONTEST, REAL), server, currency |
| GET | `/v1/symbols?names=` | tick size, volume min and step, currency |
| GET | `/v1/quotes?names=` | bid, ask, time |
| GET | `/v1/balances` | account currency balance and net positions |
| POST | `/v1/orders` | LIMIT (pending, GTC or DAY) or MARKET; demo only |
| GET | `/v1/orders/{client_order_id}` | status, fills, average price, fee |
| POST | `/v1/orders/{client_order_id}/cancel` | removes a pending order; demo only |

## Still to do

1. **XP-2:** the Windows VM (Terraform, private subnet, reachable only from the executor),
   its bootstrap (Python, MT5 terminal, the bridge as a service) and the executor routing XP_B3
   orders to the bridge.
2. **XP-3:** B3 quotes from the bridge into the Risk Engine and the strategy, B3 session hours.

What the operator needs: an XP MT5 **demo** account (login, password and server name), stored in
the Vault as `proto-xp-mt5-login`, `proto-xp-mt5-password` and `proto-xp-mt5-server`.
