# Trader-agent roster

Each trader agent gets a **virtual R$200** to trade with. If it loses that money it is
eliminated and a new agent takes its place. An eliminated agent's code is never used again.
The capital is a sizing budget inside the paper or testnet account. It is not money:
`financial_connectivity` and `real_money_execution` stay `false`.

- Code: `services/agents/roster.py` (rules, book, durable roster)
- API surface: `apps/api/app/roster_surface.py`
- Wiring: `apps/api/app/agent_surface.py`
- Tables: `agent_roster`, `agent_orders` (migration `0012_agent_roster`)
- Switch: `AGENT_ROSTER_ENABLED=true`. It is off by default, and while it is off the
  account-level pipeline works exactly as before. The roster can be read either way.

## Agents and slots

- **Slot.** A slot is one kind of signal on one instrument. There are 6 to begin with:
  PROTO's own EMA cross (`proto-ema-cross-1m`) and TradingView alerts, each on
  `BINANCE_SPOT:BTCUSDT`, `ETHUSDT` and `SOLUSDT`. Each slot has at most one ACTIVE agent.
  Seeding fills every empty slot. Running it again changes nothing.
- **Code.** A code looks like `AG-0007-SIRIUS`. The number comes from a sequence that only goes
  up, and a unique constraint guards it. The name comes from a fixed list. Rows are never
  deleted, so a code is never handed out twice.
- **Record.** Each agent stores its slot, params, `capital_brl` (200), its status (`ACTIVE` or
  `ELIMINATED`), `created_at`, `eliminated_at`, `elimination_reason` and `generation`.
  `generation` counts how many agents the slot had before this one.
- **Params.** These are fixed in advance and change with each generation.
  - EMA agents use (fast, slow) = 5/20, then 8/21, 12/26, 3/13, 10/30, 6/18, 4/16, 15/40, and
    then the list starts over. The own-strategy feed runs one EMA instance per active
    parameter set.
  - TradingView agents ignore alerts weaker than `min_strength`. The steps are 0, 0.2, 0.4,
    0.5, 0.6 and 0.7.
  - With `AGENT_LAB_STRATEGIES_ENABLED=true` (off by default), an own-strategy agent spawned
    from then on trades its instrument's `LAB_PARAM_LADDER` entry instead of an EMA pair: a
    strategy-lab spec run by `services.research.live.LabSignalStrategy`, emitting
    `proto-lab-*` signals that only that agent accepts. Agents already ACTIVE keep their EMA
    params until replaced. `AGENT_LAB_WARMUP_KLINES=true` seeds its bars from Binance public
    klines instead of waiting weeks for live mids. Evidence and caveats:
    `docs/research/strategy-lab-2026-10-19.md`.

## Routing and attribution

- **Routing.** A signal goes to the ACTIVE agent of its slot. That agent fuses only its own
  slot's signals.
- **Proposals.** The agent's proposal passes through the same reviewer, Risk Engine permit and
  OMS as before. Agents never place orders themselves.
- **Eliminated codes.** A proposal from an ELIMINATED or unknown code is dropped *before* the
  Risk Engine (`submit_for_agent`). The roster also refuses to link a new intent to that code.
- **Mapping.** The `intent_id → agent_id` mapping is written to the API's database before the
  intent is sent. The executor's wire contract does not change.
- **Fills.** Fills come back from the OMS orders the API already reads (from the executor, or
  from the in-process paper OMS). Each order is applied as the change since the last read, using
  its cumulative filled quantity, notional and fee. Reading the same order twice changes nothing.

## Book and equity

- **Book.** The book is kept in the instrument's quote currency. It holds the position quantity,
  the cost at average price, realized P&L and fees.
- **Coin fees.** A fee charged in coins removes those coins from the position at their average
  cost.
- **Equity.** `equity = 200 + (realized − fees + unrealized) × BRL-per-quote`. Unrealized P&L is
  marked at the live mid, and a mark older than 30 s does not count.
- **FX rate.** BRL-per-USDT is the public USDTBRL ask, the same rate used for the canary's R$500
  cap (`apps/api/app/fx_rates.py`).
- **Missing data.** If the mark or the FX rate is missing, equity is **unknown**. The agent then
  abstains. It is never eliminated on a guess.
- **Storage.** Decimals are stored as exact text (see `services/oms/store.py`).

## Survival instinct (only tightens)

All of these sit on top of every global check: the constitutional limits, the reviewer, the
Risk Engine and the OMS. None of them can raise a limit.

- **Exposure cap.** An agent's position is worth at most 1.0x its own BRL equity.
- **Size.** Size is multiplied by `(equity / 200)^2`, kept between 0 and 1.
- **Entry bar.** The bar for a new entry is `min_score = 0.6 + 0.3 × (1 − equity/200)`, with the
  ratio kept between 0 and 1 and the result capped at 1. Gains never lower it.
- **Exits.** A SELL is allowed, but it sells only the agent's **own** position. It never sells
  other holdings on the account, such as the testnet faucet coins, which belong to no agent.

## Elimination and replacement

- **Floor.** An agent is ELIMINATED when its equity falls below the smallest order it could
  place: the venue's minimum notional converted to BRL, and never less than R$30. The reason is
  recorded.
- **Replacement.** A new agent takes the slot. It has a new code, a fresh R$200, generation + 1,
  and the next params in the list.
- **Capital cap.** The R$200 of all ACTIVE agents together never goes over the R$10,000
  reference capital. A spawn that would go over is refused, and the slot stays empty.

## Journal and floor

| Event | Floor station |
|---|---|
| `AGENT_SPAWNED` | fusion |
| `AGENT_TRADE_ATTRIBUTED` | oms |
| `AGENT_ELIMINATED` | risk (alert) |

`AGENT_DECISION` also carries `agent_id` when an agent made the decision.

## API

`GET /v1/agents/roster` is read-only. For each agent it returns:

- code, slot, params, status and generation;
- capital, equity, P&L in BRL and in the quote currency;
- the book and `eliminated_at`.

It also returns totals: allocated, unallocated and reference capital. Every response says
`capital: VIRTUAL`, gives the `data_mode` (PAPER or TESTNET) and has `financial_connectivity` and
`real_money_execution` set to `false`. An equity that cannot be valued is returned as `null` with
`valuation: UNAVAILABLE`.

## Known limits

- **Faucet coins.** The global Risk Engine still counts the testnet faucet holdings (1 BTC,
  1 ETH, 4 SOL) as account exposure. That check is kept, so it can still block an agent's buy.
- **Leftover position.** An agent is eliminated only once its equity is below the minimum order,
  so whatever position it still holds is too small to sell. That leftover stays on the account
  and belongs to no agent.
