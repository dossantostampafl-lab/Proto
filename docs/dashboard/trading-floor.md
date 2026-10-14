# Trading floor view (Phase 7 preview)

The dashboard opens on **Floor**: a live 3D trading room (three.js), loaded on demand only on this
view. The camera sits high behind the central desk and drifts slowly; the curved wall of screens,
the three monitors on each desk and the floating desk labels are live canvas textures fed by
`/v1/floor/state`, `/ws/floor` and the public market stream. Operators are procedural figures:
they type and lean in only while their desk is working (a journal event arrived in the last few
seconds over a connected stream), and otherwise breathe and glance at their screens. A supervisor
walks the room and goes to a desk that raised an alert. The desk LEDs and the floor lines take the
state colour of the desk's last action. With reduced motion the camera and idle movement stop.

If WebGL or OffscreenCanvas is unavailable, the GPU context is lost or the 3D chunk fails to load,
the photo room is shown instead: a photograph with every screen blank
(`apps/web/src/assets/floor-room.webp`) with the same live screens drawn over it. If the photo
cannot load either, a vector drawing of the room (`floorSceneVector.tsx`) is shown. Nothing
operational is baked into any picture; the contract check enforces it.

Each step of the authority chain has a desk and an agent:

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
- the drawdown against the 5% limit;
- an **Últimas ações** strip with the latest agent actions.

Each desk has three monitors: two live public mids and the desk's own journal events.

A missing value is shown as `—` and is never estimated.

## Promotion path

The **Promotion path** panel lists every scope from `/v1/promotion` (see
`docs/promotion/gates.md`). For each one it shows:

- the stage ladder;
- the clean days and the violations;
- every reason the next stage is still refused, as the gates report it.

The **Últimas ações** strip ends with the most advanced scope's stage and its next stage.

## Watchdog

The **Watchdog** panel shows what the API last saw of the independent watchdog
(`docs/watchdog/watchdog.md`):

- whether its heartbeat is active, stale or never seen;
- the time of the last beat;
- the unbroken coverage against the 24 h the CANARY gate requires;
- how many halts it triggered, and the reasons for the last one.

The watchdog can only halt. Nothing on this panel releases a halt.

## Why each decision was taken

Each entry in the **Agent feed** can open a **why** list with the decision's own justification, copied
from the journaled event and never inferred:

- **Fusion:** the fusion reason, the outcome, any abstention, the candidate (side, quantity, limit
  price) and the sizing inputs (conviction, risk budget, sizing equity, leverage headroom).
- **Reviewer:** its rationale and size factor. The reviewer can only shrink or block.
- **Risk Engine:** every rejection, not only the first three in the headline.
- **Watchdog halts:** every reason it reported.

An event that carries no reason shows no **why** list.

## Phones

On narrow screens the scene pans sideways and opens centred on the desks. Below it, a card per
desk shows the same live headline in readable type.

## Controls

**Halt all trading** triggers the global kill switch (`POST /v1/risk/killswitch`) and requires the
operator token. Releasing a halt stays a deliberate operator action.
