# ADR: Routing XP (B3) orders and quotes through the API

- Status: Accepted for the XP **demo** account (`XPMT5-DEMO`). Nothing here certifies a live scope.
- Builds on: `docs/adr/venues-and-executor-hosting.md`, `docs/venues/xp-mt5.md`,
  `docs/executor/isolated-executor.md` ("One executor per venue").

## Context

Until now the API spoke to one executor (Binance testnet), through one constant venue, one account
id and one instrument catalog. The XP adapter, the MT5 bridge and a per-venue executor exist, but
nothing sent a B3 intent to them, and the Risk Engine, the agents and the account view knew only
Binance.

## Decision

**One executor per venue, selected by the intent's venue.** The API stays credential-free: it
holds a bearer token per executor and never talks to the MT5 bridge.

```text
signal ─> agents ─> Risk Engine ─permit─> OMS dispatch ─┬─ intent.venue = BINANCE_SPOT ─> Binance executor
                                                        └─ intent.venue = XP_B3        ─> XP executor ─> bridge ─> MT5 demo
```

### Off unless configured

`XP_EXECUTOR_URL` and `XP_EXECUTOR_API_TOKEN` (and `OMS_EXECUTION_BACKEND=executor`) turn it on.
Without them the API is as it was: the B3 instruments are not in any catalog the API uses
(`instrument_registry.active_instruments()` returns `PAPER_INSTRUMENTS`), an XP intent is refused
(`xp_routing_not_configured`) and never reaches any executor, and BRL is read as before.
Validation fails closed: the XP token must be 32+ characters and differ from the operator,
watchdog and Binance executor tokens.

### What changes when it is on

| Piece | Behaviour |
|---|---|
| Catalog | `B3_INSTRUMENTS` (`PETR4F`, `VALE3F`, `BOVA11F`: lot 1, tick 0.01, BRL, exchange session, `America/Sao_Paulo`) join the Risk Engine, the agents and the account view. They are **not** in `PAPER_INSTRUMENTS`, which the drills, the strategy and the paper venue keep iterating. |
| Dispatch | `oms_surface._dispatch` picks the client by `intent.venue`. The executor refuses intents for another venue (422) as a second guard. |
| Quotes | `b3_quotes.B3QuoteFeed` polls the XP executor's `GET /v1/quotes` every 2 s. A quote keeps the **terminal's own timestamp**, so the agents and the Risk Engine refuse it by age once the market closes or the feed stops. A symbol that turns unusable is dropped, never kept at an old price. |
| Session | The Risk Engine's existing `session_open` check applies (B3 regular hours in São Paulo time). |
| Account | BRL equity, positions and checkpoints come from the **XP ledger** (`executor-xp-b3` checkpoints, separate from Binance's). USDT stays on Binance. Opening holdings are outside the book, as for Binance. |
| Halts | A reconciliation divergence or a halted executor kills **its venue only** (`KillScope.VENUE`) and records a promotion violation for that venue. |
| Routes | `GET /v1/oms/orders`, `POST /v1/oms/orders/{id}/resolve\|cancel` and `POST /v1/oms/executor/halt/release` take `?venue=BINANCE_SPOT\|XP_B3` (default Binance). `POST /v1/oms/reconcile` and `GET /v1/oms/ledger` add an `xp_b3` block. |

### Fail-closed behaviour

- XP executor unreachable: `account_state("BRL")` takes the outage path (infinite exposure blocks new
  risk), exactly as for Binance.
- No fresh B3 quote: the position cannot be marked, so exposure is infinite and new risk is refused.
- A permit is still required for every order; the executor still verifies it and refuses CANARY
  and LIVE intents.

## Known limits (not solved here)

1. **Loss limits on a large demo balance.** The demo account holds R$ 1,000,000. The daily-loss and
   drawdown limits are fractions of the real equity, so 1% of that is R$ 10,000, not the R$ 100 the
   R$ 10,000 reference capital implies. Sizing and leverage are capped by `conservative_equity`, but
   the loss brake is effectively off. Tracked as its own change (measure the loss against
   `min(equity, reference capital)`, which only tightens). **Do not enable XP routing before it
   lands.**
2. **The watchdog does not probe the XP executor.** Acceptable for a demo; not for anything beyond.
3. **MT5 balance semantics.** Whether the terminal's BRL balance moves when shares are bought
   (netting, B3) decides if reconciliation stays consistent after a fill. It is only proven with a
   fill during a session; until then expect a possible divergence halt, which is the safe outcome.
4. **Hosting.** The terminal needs Windows on x86. See `docs/adr/xp-mt5-hosting-oracle.md`.
