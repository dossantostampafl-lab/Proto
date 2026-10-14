# Trading floor view (Phase 7 preview)

The dashboard opens on **Floor**: an isometric pixel-art trading floor, drawn in code (SVG) with bloom lighting, light pools, a layered rainy city and a curved wall screen. Each
step of the authority chain has a desk and an agent:

TradingView → Fusion → AI Reviewer → Risk Engine → OMS / Executor → Reconciliation

## Live data

- **`/ws/floor`.** Every operational-journal event is pushed here as soon as it is recorded. A
  `TRADINGVIEW_SIGNAL_ACCEPTED`, `AGENT_DECISION`, `RISK_*`, `ORDER_*` or
  `RECONCILIATION_DIVERGENCE` event becomes a *floor action* for its station:
  - **What an action carries:** a tone (`ok`, `warn`, `alert`, `working`), a short headline and the
    symbol.
  - **What it never carries:** permit signatures, prompts or credentials.
  - **On screen:** the agent at that desk types, its monitors flicker and the flow beam into the
    desk lights up for a few seconds.
- **`/v1/floor/state`.** Bootstraps the view and is re-read every 5 seconds. It returns the recent
  actions, the latest action of each station, the controls (kill switches, lockdown, journal
  integrity), the accounts per quote currency and the pipeline configuration.
- **Data mode.** The data mode is always visible: `PAPER / SIM`, or `TESTNET · NO REAL MONEY` when
  `OMS_EXECUTION_BACKEND=executor`.

## Wall screen

The wall screen shows:

- live public mids for BTC, ETH and SOL;
- the USDT account's equity;
- the daily result against the 1% limit;
- the drawdown against the 5% limit.

A missing value is shown as `—` and is never estimated.

## Promotion path

The **Promotion path** panel lists every scope from `/v1/promotion` (see
`docs/promotion/gates.md`). For each one it shows:

- the stage ladder;
- the clean days and the violations;
- every reason the next stage is still refused, as the gates report it.

The wall screen header shows the most advanced scope's stage and its next stage.

## Phones

On narrow screens the scene pans sideways and opens centred on the desks. Below it, a card per
desk shows the same live headline in readable type.

## Controls

**Halt all trading** triggers the global kill switch (`POST /v1/risk/killswitch`) and requires the
operator token. Releasing a halt stays a deliberate operator action.
