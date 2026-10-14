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

1. **Permit.** `PermitLedger.consume` checks the signature, fingerprint and expiry, and allows a
   single use. Without a valid permit the OMS does nothing.
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

## Ledger (`services/oms/ledger.py`)

Balances are sums of signed entries; no total is stored as a mutable number.

| Field | Formula |
|---|---|
| `total` | `OPENING_BALANCE + TRADE + FEE` |
| `reserved` | `−(RESERVATION + RELEASE)` |
| `available` | `total − reserved` |

- A batch is posted atomically, in one database transaction with `SqlLedger` (migration
  `0008_ledger_entries`). Entry ids are deterministic, so re-posting a batch is a no-op and a
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
- **Start-of-day equity** resets on each UTC day; **peak equity** is tracked continuously. Together
  they drive the 1% daily-loss and 5% drawdown lockdowns.
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

- Orders live in memory with their full history in the journal; rebuilding open orders from the
  journal after a restart belongs to the recovery gate (Phase 6).
- Resting limit orders are not matched later by the paper venue. They rest until canceled.
- The Binance and XP adapters arrive in Phase 5 and must pass the same OMS and reconciliation tests.
