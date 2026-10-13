---
version: beta
colors:
  canvas: "#02040A"
  panel: "rgba(6, 14, 26, 0.78)"
  text: "#E8F6FF"
  muted: "#6F8AA6"
  accent: "#00F0FF"
  accent2: "#FF2BD6"
  positive: "#3DFFA2"
  negative: "#FF3B5C"
  warning: "#FFCC33"
  paper: "#FFB020"
typography:
  display: "Chakra Petch (self-hosted, latin 500/700)"
  data: "JetBrains Mono (self-hosted, latin 400/600)"
shape:
  panelCut: "14px angled corners + HUD corner brackets"
  control: "8px angled corners"
---

## Overview

PROTO is a sci-fi HUD command deck for research, live public market telemetry, simulation, paper trading
and replay. The north star is a starship cockpit: dark, luminous, precise. Every surface is either a live
reading, a research output or a paper/simulation state — and says which.

The single source is `apps/web/src`: one React tree (`main.tsx`), a typed API client (`api.ts`), shared hooks
(`hooks.ts`), primitives (`ui.tsx`) and five views: **Command · Trade · Lab · Replay · System**.

## Signature elements

- **Probability orbit** (Command): outer cyan arc = market-implied probability, inner magenta arc = model
  probability of the selected market; the gap between them is the edge, shown in the core. Every research
  market orbits at a radius proportional to its expiry horizon. A radar sweep and 60 rim ticks animate it.
- **Ticker tape** under the header with live prices, mode, kill-switch state and the financial boundary.
- **HUD panels**: angled corners, corner brackets, a scanning highlight, staged "boot" reveal per view.
- **Atmosphere**: perspective grid floor, cyan/magenta corner glows, faint scanlines.

## Colors

Cyan (`accent`) is the live/primary signal; magenta (`accent2`) is reserved for synthetic research.
Green/red always mean positive/negative, amber means caution, orange (`paper`) tags paper/simulation.
Glow is used for primary numbers and active state only — not for body text.

## Data provenance (mandatory)

Every panel carries a provenance tag:

- `LIVE PUBLIC` — price, bid/ask, spread, sizes, L1 microstructure and live analytics from the public
  read-only feed.
- `SYNTHETIC RESEARCH` — lifecycle grid, market/model probability, net edge, the orbit, synthetic Greeks,
  Hawkes state, calibration. Never labelled as live.
- `PAPER / SIM` — paper engine, orders, autopilot, positions, P&L, replay.

The footer always shows `FINANCIAL CONNECTIVITY OFF` and `REAL MONEY EXECUTION OFF`. The composite risk
number is labelled **Display risk score** because it is not a canonical risk limit.

## States

Loading reads "acquiring signal"/"checking"; missing data shows `—`; disabled research explains why
(`503` → "disabled in this deployment"); stale feed is shown as stale. Never invent values to fill space.

## Interaction

Operator commands go through one `Button` → `run()` path that attaches `X-Proto-Operator-Token` (stored
in `sessionStorage` from the System view) and records the result in the Operator log. Buttons are
disabled when the action is not valid for the current state.

## Responsiveness & accessibility

Single column below 900px with no horizontal page scroll; header nav scrolls horizontally if needed.
Visible focus rings, `aria-live` status bar, `role="meter"` bars, `aria-pressed` toggles.
`prefers-reduced-motion: reduce` disables all animation.

## Don'ts

Don't invent performance data, fills or balances. Don't style research like live data. Don't add
real-money affordances (deposit, withdraw, broker orders). Don't inject HTML or patch globals.
`npm run contract-check` enforces these rules.
