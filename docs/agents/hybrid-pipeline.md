# Hybrid agent pipeline

Phase 2 of `docs/plans/2026-10-13-tradingview-agent-trading.md`. Deterministic code decides what
to trade and how much. An AI reviewer may only make that trade smaller or stop it. The result is
a **paper proposal** with full lineage and no order authority. Phase 3's Risk Engine decides
whether a proposal may become an order.

```text
TradingView / PROTO signals ─> fusion ─> sized candidate ─> reviewer ─> proposal (journaled)
                               (deterministic)                (shrink-only)
```

## Fusion (`services/agents/fusion.py`)

- Uses only the latest signal of each source and strategy pair received within the last 5 minutes.
  Signals stamped in the future are ignored.
- The weighted score is the sum of `direction × strength × weight` divided by the total weight.
  Weights are TradingView 1.0, PROTO strategies 1.0 and PROTO agents 0.5.
- `LONG` needs a score of at least 0.6. `SHORT` needs at most −0.2 (`exit_min_score`): on spot a sell only closes a holding (sizing refuses a short without one), so an exit needs a reason, not the conviction of an entry. With one 0.6 bar for both, a position opened on a strong cross almost never closed (backtest: no completed trades in two weeks). Anything in between is `FLAT`.
- Opposing signals that both have strength ≥ 0.5 are a conflict, and the result is `FLAT`.

## Sizing (spot only)

- **LONG → BUY.** The quantity comes from the per-position risk limit:
  `risk = min(equity, R$10,000) × 0.25% × conviction`, and
  `quantity = risk ÷ (price × stop_distance)` (3% by default, `AGENT_STOP_DISTANCE`).
  - The per-position budget counts what is already held: a buy only fills the room left in it,
    and the pipeline abstains with `risk per position limit reached` when none is left.
  - The total position is capped at 1.0× of that equity (gross leverage limit).
  - The quantity is floored to the venue's lot size, and the price is rounded to its tick.
  - The order is a LIMIT at the ask with time in force IOC.
- **SHORT → SELL.** Sells only existing holdings (`reduce_only`). Spot cannot open a short, so
  the pipeline abstains when there is nothing to sell.
- **Protective stop and target (own-strategy EMA agents).** When the bid falls `AGENT_STOP_LOSS` (3%)
  under, or rises `AGENT_TAKE_PROFIT` (6%) above, the agent's own average cost, a SHORT of strength 1.0 is journaled as a `PROTO_STRATEGY_SIGNAL`
  (timeframe `stop` or `target`) and goes through fusion, the Risk Engine and the OMS like any exit. It is
  retried once a minute while the position is still there. The stop is never wider than the stop
  the position was sized on (settings validation). TradingView agents exit on their alerts only.
- **Bars and pair.** The EMA agents run on `AGENT_EMA_BAR_MINUTES` bars (1 to 10, default 10; an
  agent's own `bar_minutes` wins) with the fleet-wide pair `AGENT_EMA_FAST`/`AGENT_EMA_SLOW` (20/50;
  0/0 keeps each agent's ladder pair) and `AGENT_EMA_COOLDOWN_MINUTES` (600) between signals. Why:
  `docs/research/short-timeframes-2026-10-28.md`.
- **Warm-up and trend filter.** With `AGENT_EMA_WARMUP_KLINES=true` (production image) the EMA bars
  are seeded from Binance public 1m klines at start, and an entry (LONG) only goes out while the mid
  is above the 1h EMA200 (`AGENT_EMA_TREND_FILTER`). A trend that cannot be read blocks entries,
  never exits. B3 and US agents are not filtered (no Binance klines for them).
- **The pipeline abstains when:**
  - the quote is missing, older than 10 seconds, or crossed;
  - the order would fall below the venue minimum;
  - the gross leverage limit is already reached.

## Review (`services/agents/review.py`)

| Verdict | Size factor | Effect |
|---|---|---|
| `APPROVE` | 1 | Proposal equals the candidate |
| `REDUCE` | 0 < f < 1 | Quantity × f, floored to the lot. Below the venue minimum it becomes `ABSTAINED` |
| `DEFER` / `VETO` | 0 | No proposal |

- **`deterministic` (default):** approves every sized candidate. This is the baseline.
- **`claude`:** sends one structured-output request (`claude-opus-5-5`, effort `low`, 15 s
  timeout, no retries, server-side refusal fallback) with JSON context:
  - candidate, fusion, quote and spread;
  - equity and position;
  - supporting signals and constitutional limits.

  The reviewer never sees venue credentials. Any timeout, API error, refusal, `max_tokens` stop
  or answer that breaks the contract becomes `DEFER`. A configured but unbuildable client also
  defers everything; it never falls back to approve-all.

## Enabling the AI reviewer

Run `services.agents.evaluate_reviewer` over at least 50 historical candidates with known
outcomes. The reviewer is promotable only if the reviewed book's PnL is not below the
deterministic baseline **and** its maximum drawdown is not deeper. Only then set
`AGENT_REVIEWER=claude`.

## API

| Route | |
|---|---|
| `GET /v1/agents/status` | Enabled flag, reviewer, outcome counts, journal failures |
| `GET /v1/agents/decisions/recent` | Latest decisions with candidate, review and proposal |
| `POST /v1/agents/evaluate` | Operator token required. Evaluates buffered signals for one instrument |

With `AGENT_PIPELINE_ENABLED=true`, every accepted TradingView signal triggers an evaluation in
the background, so the webhook still answers immediately. Each decision is written to the
operational journal as `AGENT_DECISION`, with the proposal's fingerprint and the prompt's SHA-256.

## Current limitations (closed by later phases)

- Live quotes exist only for `BINANCE_SPOT:<BASE>USDT` while `LIVE_MARKET_SOURCE=BINANCE`. BRL
  pairs abstain with `no market data` until a BRL feed is added.
- Equity and positions come from the paper ledger (Phase 4), so SHORT signals exit positions the
  paper account actually holds.
- Instrument grids in `services/execution/catalog.py` are paper approximations. The Binance
  adapter loads the real ones from `exchangeInfo` (`docs/venues/binance-spot.md`); the agents
  switch to them when the executor runs (Phase 5b).
