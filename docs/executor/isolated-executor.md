# Isolated executor (Phase 5b)

This is Phase 5 of `docs/plans/2026-10-13-tradingview-agent-trading.md`, second part. The executor
is the only process that holds venue credentials. It runs the canonical OMS, ledger and
reconciliation against the Binance Spot adapter (`docs/venues/binance-spot.md`), on the **testnet
only**.

```text
TradingView ─> public API ─> agents ─> Risk Engine ─(intent + permit)─┐  private subnet
                  │  (no venue credentials)                            v
                  │                                       executor (apps/executor)
                  │<── orders, balances, reconciliation ──┤  OMS · ledger · reconciliation
                  │                                       └─> Binance Spot testnet (signed REST)
```

## What the executor guarantees

- **Authority.**
  - It accepts work only as intent and Risk Permit pairs, over the private network, with the
    bearer token `EXECUTOR_API_TOKEN`.
  - Each permit is verified (signature, fingerprint, expiry) and spent durably before the order is
    sent.
- **Idempotent dispatch.** A retried dispatch of the same intent returns the order the executor
  already holds, and nothing is sent twice. That makes a lost response between the API and the
  executor safe to retry.
- **Restart.**
  - On start it reloads the persisted order book. Open orders come back as `UNKNOWN` and are
    resolved by querying Binance.
  - It reconciles before it accepts any order.
  - The first start opens the ledger from the account's current balances.
- **Resting orders.** Working orders are polled (`EXECUTOR_POLL_SECONDS`, default 5 s), so fills on
  resting limit orders reach the ledger. An order that Binance acknowledged but no longer knows
  becomes `UNKNOWN`; it is never guessed.
- **Halt.**
  - The ledger and the account are reconciled after every order and every
    `EXECUTOR_RECONCILE_SECONDS` (default 60 s).
  - A divergence halts new orders (HTTP 409).
  - `POST /v1/halt/release` is an operator action, and it is refused while the divergence holds.
  - On the API side the divergence also triggers the Risk Governor's venue kill switch.
- **No real money.** `EXECUTOR_ENVIRONMENT` accepts only `TESTNET`, and a real-money adapter is
  refused at start. `/health` reports `real_money_execution=false` and
  `financial_connectivity=false`, because testnet funds are not money.
- **Isolation.** `tests/unit/test_venue_isolation.py` fails if the public API imports
  `apps.executor` or `services.venues`, or mentions the credential variables.

## Private API

| Route | |
|---|---|
| `GET /health` | No token. Status (`ok`, `halted`, `starting`) and the boundary flags |
| `POST /v1/orders` | `{intent, permit}`. 409 when halted, 422 when the permit or intent is refused |
| `GET /v1/orders` | Latest orders |
| `POST /v1/orders/{id}/resolve` · `/cancel` | Resolve an `UNKNOWN` order by query; cancel a resting order |
| `GET /v1/ledger` | Balances per asset |
| `POST /v1/reconcile` · `POST /v1/halt/release` | Reconcile now; release a halt once consistent. Operators can relay the release through the API: `POST /v1/oms/executor/halt/release` |
| `GET /v1/quotes?names=` | Venue quotes (XP_B3: from the MT5 terminal; ALPACA_US: Alpaca's IEX feed, labelled `feed: IEX`); 404 on a venue without a quote feed |
| `GET /v1/events` | Recent order, reconciliation and halt events |
| `GET /v1/watchdog/status` · `POST /v1/watchdog/halt` | Watchdog token only (`EXECUTOR_WATCHDOG_TOKEN`): readiness, halt, last reconciliation; halt. It cannot trade or release (`docs/watchdog/watchdog.md`) |

## One executor per venue

`EXECUTOR_VENUE` picks the venue this process serves: `BINANCE_SPOT` (default), `XP_B3` or
`ALPACA_US` (Alpaca **paper** account only, `docs/venues/alpaca-paper.md`). Each
executor holds only its own venue's credentials and has its own ledger, halt and reconciliation,
so the isolation of the credentials is unchanged. Run a second instance for XP_B3:

```bash
export EXECUTOR_VENUE=XP_B3
export EXECUTOR_BRIDGE_URL=http://<mt5-host>:<port>      # private network only
export EXECUTOR_BRIDGE_TOKEN=...                         # differs from EXECUTOR_API_TOKEN
export EXECUTOR_SYMBOLS=PETR4F,VALE3F,BOVA11F            # required: no default for XP_B3
```

- The ledger account id carries the venue (`xp-b3-testnet-main`). The Binance id
  (`binance-spot-testnet-main`) is unchanged, so its persisted ledger keeps working.
- The bridge reports the logged-in account. A demo account maps to `TESTNET`; anything else makes
  the adapter real-money and the executor refuses to start.
- An executor refuses intents for another venue (422).
- `ALPACA_US` needs `EXECUTOR_SYMBOLS` (e.g. `SPY,QQQ,AAPL`) and the paper keys as
  `PROTO_CRED_ALPACA_US_TESTNET_MAIN_API_KEY` / `_API_SECRET`. Its ledger id is
  `alpaca-us-testnet-main`; the trading host is pinned to `paper-api.alpaca.markets`.
- The independent watchdog and the public API still talk to the Binance executor only. Routing
  XP_B3 intents and B3 quotes through the API is a separate change; until then the XP executor is
  reachable only by hand and stays outside the watchdog's coverage, which is acceptable for a demo
  account and not for anything beyond it.

## Public API with the executor

Set these on the public API:

```bash
OMS_EXECUTION_BACKEND=executor
EXECUTOR_URL=http://10.0.2.10:8100          # executor's private address
EXECUTOR_API_TOKEN=<same value as the executor>
RISK_PERMIT_PRIVATE_KEY=<Ed25519 seed matching EXECUTOR_PERMIT_PUBLIC_KEY>
```

- **Execution.** Approved proposals are dispatched to the executor. `/v1/oms/*` then reads orders,
  balances and reconciliation from it, and the ledger view reports `mode: TESTNET`.
- **Risk.** The Risk Engine and agent sizing use the executor's balances, marked at live prices.
- **Journal.** It records `ORDER_*`, `ORDER_REFUSED`, `ORDER_DISPATCH_UNKNOWN` and
  `RECONCILIATION_DIVERGENCE`.
- **Executor unreachable.**
  - The account state reports infinite exposure and no known positions. That blocks new risk,
    and sells are refused too, because positions are unknown.
  - Dispatch is journaled as `ORDER_DISPATCH_UNKNOWN` and nothing is assumed.

## Running it on Oracle Cloud

The whole layout (network, Vault, IAM, instances) is Terraform in `infra/oci/`. Follow
`docs/runbooks/oracle-setup.md`. The commands below are what runs on the executor instance.

The executor uses the same image as the API, with a different command. Run it on its own instance
in the private subnet, with no public IP. Egress goes through the NAT gateway
(`docs/adr/venues-and-executor-hosting.md`).

```bash
# Secrets come from OCI Vault into tmpfs at start, never from the image or the repository.
export EXECUTOR_API_TOKEN=...        # from the secret store
export EXECUTOR_ENVIRONMENT=TESTNET
export EXECUTOR_ACCOUNT=main
export EXECUTOR_DATABASE_URL=postgresql+asyncpg://executor:...@10.0.2.20:5432/executor
export EXECUTOR_PERMIT_PUBLIC_KEY=...      # Ed25519 public key (python -m services.risk.permit_keys)
export EXECUTOR_SYMBOLS=BTCUSDT,ETHUSDT,SOLUSDT
export PROTO_CRED_BINANCE_SPOT_TESTNET_MAIN_API_KEY=...
export PROTO_CRED_BINANCE_SPOT_TESTNET_MAIN_API_SECRET_FILE=/run/secrets/binance-testnet-secret
uvicorn apps.executor.main:app --host 10.0.2.10 --port 8100
```

- **Before the first run:** check the keys with `python -m services.venues.binance_smoke`.
- **Database:** use its own database, not the public API's.
- **Network access:** allow port 8100 only from the API instance's security group.

## Current limitations

- **Permit key.** Set `EXECUTOR_PERMIT_PUBLIC_KEY` (Ed25519, see `docs/promotion/gates.md`) so the
  executor can only verify permits. The HMAC key fallback lets it mint them too.
- **Stages.** The testnet executor refuses CANARY and LIVE intents.
- **Fills on resting orders** arrive by polling. A user-data WebSocket stream would lower latency
  and can replace it.
- **Halt state** lives in memory. It re-latches on start, because the executor reconciles before
  taking orders.
- **Grids.** The agents still size with the paper grids. The executor rejects orders that do not
  fit the real grid from `exchangeInfo`.
- **Testnet resets.** Binance resets testnet balances from time to time. That shows up as a
  divergence and halts the executor until an operator reconciles and releases it.
