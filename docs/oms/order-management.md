# OMS, ledger and reconciliation

Phase 4 of `docs/plans/2026-10-13-tradingview-agent-trading.md`. The OMS owns order state, the
ledger owns balances and positions, and reconciliation proves that both match the venue. Execution
is **paper only**: a simulated venue stands in for `BINANCE_SPOT`, and the OMS refuses any adapter
that reports `real_money_execution=true`.

```text
TradingView ─> agents ─> Risk Engine ─(permit)─> OMS ─> paper venue (live public quotes)
                                                  │
                                                  └─> ledger ─> reconciliation ─(divergence)─> venue kill switch
```

## Order lifecycle (`services/oms/manager.py`)

1. **Permit.** The signature, fingerprint and expiry are checked, and the permit is spent once.
   With persistence the spend is a primary-key insert in `oms_consumed_permits`, committed before
   the order is sent, so a permit stays spent across restarts and processes. Without a valid permit
   the OMS does nothing.
2. **Reservation.** A buy reserves `quantity × limit × 1.005` in the quote asset; a sell reserves the
   base quantity. When funds are short the order becomes `REJECTED` and the venue is never called.
3. **Submit.** The client order id is derived from the intent (`intent.client_order_id(0)`).
   Submitting the same intent again returns the existing order.
4. **Reports.** Venue reports move the order through the state machine. Each fill posts its
   increment: base and quote `TRADE` entries, a `FEE` entry, and a proportional `RELEASE` of the
   reservation. Whatever is still reserved at a terminal state is released.
5. **Unknown outcomes.** A timeout or network error during submit leaves the order `UNKNOWN`.
   `resolve_unknown` asks the venue. If the venue has the order, its report is applied; if the
   venue confirms it never received it, the order is `REJECTED` and the reservation is released.
   An `UNKNOWN` order is **never resent**.

## Durable state and restart recovery (`services/oms/store.py`)

Persistence here is the control-plane store (`CONTROL_PERSISTENCE_ENABLED`, on in the image and
allowed in production LIVE_MONITORING; see `docs/runbooks/control-plane-persistence.md`). It is
separate from simulation persistence (`PERSISTENCE_ENABLED`). With it, every order state change is written to `oms_orders` before the OMS moves
on. The new order is written before its reservation is posted, so a crash never orphans funds.
Decimals are stored as exact text. On start, `OrderManager.recover()` reloads the book:

| State at restart | Becomes | Why |
|---|---|---|
| `FILLED`, `CANCELED`, `REJECTED`, `UNKNOWN` | unchanged | Already settled, or already awaiting a venue query |
| `NEW` | `REJECTED` ("interrupted before submit") | It never reached the venue. Its reservation, if posted, is released |
| `SUBMITTED`, `ACKNOWLEDGED`, `PARTIALLY_FILLED` | `UNKNOWN` ("restarted while open") | It may have changed at the venue while the process was down |

Recovered `UNKNOWN` orders are resolved with `POST /v1/oms/orders/{id}/resolve`, which queries the
venue. A fill found there is applied (fill entries are idempotent, so a fill posted just before the
crash is not counted twice). An order the venue never received is rejected and released. Nothing
is resent, and an unresolved order holds only its own reservation; it never halts the venue. Each
recovery is journaled as `ORDER_RECOVERED`. Resubmitting an intent after a restart returns the
persisted order.

## Ledger (`services/oms/ledger.py`)

Balances are sums of signed entries; no total is stored as a mutable number.

| Field | Formula |
|---|---|
| `total` | `OPENING_BALANCE + TRADE + FEE` |
| `reserved` | `−(RESERVATION + RELEASE)` |
| `available` | `total − reserved` |

- A batch is posted atomically, in one database transaction with `SqlLedger` (migration
  `0008_ledger_entries`; the OMS state tables are in `0009_oms_state`). Entry ids are deterministic, so re-posting a batch is a no-op and a
  partially overlapping batch is refused.
- Reservations are guarded: available balance cannot go negative, and a release cannot exceed what
  was reserved.
- Fills are always recorded, because the venue has already executed them. A gap between ledger and
  venue is reconciliation's job.
- `OPENING_BALANCE` records funds already held at the venue. It is not a deposit; money movement
  stays outside the system.

## Reconciliation (`services/oms/reconciliation.py`)

The ledger `total` of every asset is compared with the venue's balances. Any difference above the
tolerance (default 1e-8) triggers the venue kill switch through the Risk Governor and writes
`RECONCILIATION_DIVERGENCE` to the journal. Only an operator can release that kill switch. The API
reconciles after every paper order and on `POST /v1/oms/reconcile`.

## Account state for the Risk Engine

`account_state(quote_asset)` builds the Risk Engine's view from the ledger.

- **Equity** is quote cash plus positions marked at the live mid price.
- **Positions are PROTO's own:** total minus the `OPENING_BALANCE`. Holdings the venue already had
  (the Binance testnet faucet gives 1 BTC) are outside the book: they would swamp the R$10,000
  reference capital and swing the daily loss with prices PROTO never traded. Only what PROTO bought
  counts as exposure, and only that can be sold. The executor reports `opening` per asset in
  `/v1/ledger`; an executor that does not report it keeps the stricter reading (all of it counts).
  Only a quote currency whose base assets have opening holdings changes basis: its start-of-day
  and peak equity move to `<quote>:own` and start from the current equity once, because values saved
  with the faucet BTC inside equity would read as an ~89% loss. Every other account keeps its saved
  checkpoint, so a loss already on the books is never wiped. While the executor cannot be read, the
  same rule applies using the opening holdings seen last; with none seen (cold start) the lower
  start of day and the higher peak of the two keys are used.
- **Start-of-day equity** resets on each UTC day; **peak equity** is tracked continuously. Together
  they drive the 1% daily-loss and 5% drawdown lockdowns. Both are checkpointed per quote currency
  in `oms_equity_checkpoints`, so a restart can neither reset the daily-loss baseline nor forget
  the drawdown peak.
- **No live mark for a held asset:** gross exposure becomes infinite, which blocks new risk while
  exits stay allowed, and equity holds at its baseline so a missing quote is never read as a loss.
- **Agent sizing** uses the same equity and the position held in the ledger, so sell signals now
  exit real paper positions.

## API

| Route | |
|---|---|
| `GET /v1/oms/orders` | Latest orders with state history, fills, fees and open reservation |
| `GET /v1/oms/ledger` | Balances per asset and account state per quote currency |
| `POST /v1/oms/reconcile` | Operator. Reconciles now; halts the venue on divergence |
| `POST /v1/oms/orders/{id}/resolve` | Operator. Resolves an `UNKNOWN` order by querying the venue |
| `POST /v1/oms/orders/{id}/cancel` | Operator. Cancels a resting order and releases its reservation |

With `OMS_PAPER_EXECUTION_ENABLED=true`, approved proposals execute automatically. The journal
records every transition (`ORDER_SUBMITTED`, `ORDER_FILLED`, `ORDER_UNKNOWN`, `ORDER_REJECTED`, …).

## Paper account

The account `paper-binance-spot` opens with R$10,000 equivalent in each quote currency, BRL and
USDT. FX between them is not modelled, so each currency is a separate risk account under the same
constitutional limits. The simulated venue is re-seeded from the ledger on every start, so a
durable ledger and the venue stay reconciled across restarts.

## Current limitations (closed by later phases)

- Without a control-plane store (`CONTROL_PERSISTENCE_ENABLED=false` and no derived Postgres),
  orders, spent permits and checkpoints live in memory and are lost on restart. Spent permits expire within 60 s anyway.
- Recovered `UNKNOWN` orders are resolved by the operator, not automatically. The recovery gate
  (Phase 6) adds automated resolution and its adversarial tests.
- Resting limit orders are not matched later by the paper venue. They rest until canceled.
- The Binance Spot adapter (testnet only) runs in the isolated executor
  (`docs/executor/isolated-executor.md`), selected with `OMS_EXECUTION_BACKEND=executor`. The XP
  adapter comes next.
