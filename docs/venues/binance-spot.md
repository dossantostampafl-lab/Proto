# Binance Spot adapter (Phase 5a)

This is Phase 5 of `docs/plans/2026-10-13-tradingview-agent-trading.md`, first path. The adapter in
`services/venues/binance.py` implements the same `VenueAdapter` contract as the paper venue, so the
OMS, ledger and reconciliation from Phase 4 work with it unchanged.

**Scope:** only the Binance Spot **testnet** (`https://testnet.binance.vision`), which holds no real
money. A `LIVE` adapter reports `real_money_execution=true`, and the OMS refuses it until a live
scope is certified (Phase 6).

```text
OMS ─(VenueOrder + client id)─> BinanceSpotAdapter ─(signed REST)─> Binance Spot testnet
    <─(ExecutionReport: status, cumulative fill, fee in the charged asset)─┘
```

## Behaviour

| Situation | Adapter | OMS |
|---|---|---|
| Accepted, filled, resting, expired IOC | Report mapped from Binance status (`EXPIRED` → `CANCELED`) | Normal state machine |
| 4xx with a Binance code (balance, filters, clock skew) | `REJECTED` report with the code and message | Reservation released |
| 5xx, timeout, reset, codes `-1006`/`-1007` | Raises `VenueOutcomeUnknown` | Order `UNKNOWN`, resolved by query, **never resent** |
| Query of an order Binance never received (`-2013`) | `None` | `REJECTED`, reservation released |
| Duplicate client id | Reports the existing order | Same as a query |
| Clock skew (`-1021`) | Rejected, and the clock is resynced on the next request | `REJECTED` |

- **Identity.** The OMS client order id (`PX…`) is sent as `newClientOrderId`. Queries and
  cancels address the order by symbol and that id, so the adapter keeps no state of its own and
  works after a restart.
- **Fees** come from the fills (`newOrderRespType=FULL`, or `myTrades` on a query). Binance charges
  them in the asset it chooses: base, quote or BNB. The ledger posts the fee in that asset, so it
  stays reconciled with the account.
  - An order whose fills mix fee assets is reported as unknown rather than guessed.
  - Keep "pay fees with BNB" disabled.
- **Grids.** `load_instruments` reads tick, step, minimum quantity and minimum notional from
  `exchangeInfo`. Symbols that are missing or not `TRADING` are left out, so their orders are
  rejected instead of trusting the paper approximations in `services/execution/catalog.py`.
- **Time in force.** `VenueOrder` now carries the intent's time in force. `IOC` remainders expire as
  `CANCELED`; `DAY` is rejected because Binance spot does not offer it. The paper venue now honours
  `IOC` the same way, so an unmarketable agent order releases its reservation instead of resting.

## Credentials (`services/venues/credentials.py`)

- Credentials are scoped by venue, environment and account. They are read from
  `PROTO_CRED_<VENUE>_<ENV>_<ACCOUNT>_API_KEY` and `..._API_SECRET`.
- Each variable also has a `..._FILE` form: the executor's OCI Vault fetch writes the secret to a
  tmpfs file, and the variable points to it.
- They are never printed: `repr` and `str` leave out the key and the secret.
- Only `services/venues` reads them. `tests/unit/test_venue_isolation.py` fails if anything in
  `apps/` or `services/` outside the executor boundary imports `services.venues` or mentions
  `PROTO_CRED_`. The same test checks that the package contains no withdrawal, transfer, margin or
  futures routes.
- **Before any LIVE key is used**, `verify_trade_only()` reads `/sapi/v1/account/apiRestrictions`.
  It refuses a key that can withdraw, transfer, use margin or futures, or that has no IP
  allowlist.

## Testnet check

1. Create testnet keys at `https://testnet.binance.vision`.
2. Run:

   ```bash
   export PROTO_CRED_BINANCE_SPOT_TESTNET_MAIN_API_KEY=...
   export PROTO_CRED_BINANCE_SPOT_TESTNET_MAIN_API_SECRET_FILE=/run/secrets/binance-testnet
   python -m services.venues.binance_smoke --environment TESTNET --account main
   ```

The tool is read-only. It reports the clock offset, the grids, the assets with a balance, and
whether a signed query works. Orders go only through the OMS, behind a Risk Permit.

## Tests

`tests/unit/test_binance_adapter.py` runs the adapter against an in-process fake of the REST API.
The fake verifies every signature and timestamp. The tests cover:

- grid loading;
- fills with the fee in base or quote, reconciled through the OMS;
- definite rejections versus unknown outcomes;
- a lost submit response, resolved by query without a second `POST`;
- cancel, expired IOC and partial fills;
- duplicate ids and clock resync;
- LIVE keys that are not trade-only;
- refusal of a LIVE adapter by the OMS;
- credential redaction and file-backed secrets.

## Next (Phase 5b)

- **Isolated executor.** A separate process on the private OCI subnet holds the credentials and
  runs the OMS and ledger with this adapter. The public API sends it intents and permits over the
  private network.
- **User-data stream.** A WebSocket stream for fills on resting orders.
- **Reconciliation** of the testnet account.
