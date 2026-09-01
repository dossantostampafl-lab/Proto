---
version: alpha
colors:
  canvas: "#050811"
  surface: "#08101C"
  surfaceRaised: "#0B1320"
  border: "#162239"
  text: "#E8EEF8"
  muted: "#7E8A9C"
  info: "#71A7FF"
  positive: "#5CE39B"
  negative: "#F05F65"
  warning: "#E8C15A"
  accent: "#8E6CFF"
typography:
  display:
    fontFamily: "IBM Plex Sans Condensed, Inter, system-ui, sans-serif"
  body:
    fontFamily: "Inter, system-ui, sans-serif"
  data:
    fontFamily: "IBM Plex Mono, JetBrains Mono, ui-monospace, monospace"
rounded:
  panel: "10px"
  control: "7px"
  pill: "999px"
spacing:
  xs: "4px"
  sm: "8px"
  md: "12px"
  lg: "16px"
  xl: "24px"
components:
  frame:
    radius: "10px"
    border: "1px solid #162239"
  sourceTag:
    radius: "999px"
  control:
    radius: "7px"
---

## Overview

PROTO is a dense quantitative command terminal for research, live public market telemetry, simulation, paper trading, replay, risk and portfolio inspection. The visual north star is an institutional control room: fast to scan, information-rich and visibly stateful. Parallax-style spatial density is a reference for hierarchy and composition, not a template to copy.

The signature element is the **Expiry / Risk Field**: the torus and its surrounding probability, expiry and risk surfaces should remain the visual anchor of the research layer. Everything around it stays disciplined and utilitarian.

The product register is **product-first institutional**. Avoid consumer-crypto styling, oversized marketing cards, glassmorphism fog, gratuitous neon, fake depth, decorative gradients, and generic SaaS dashboards.

## Colors

The canvas uses `#050811`; primary panels use `#08101C`; raised or focused panels use `#0B1320`. Borders use `#162239` and should remain subtle enough to preserve density. Semantic colors have fixed meaning: green is healthy/positive, red is negative/failure, amber is caution, blue is live informational context, and violet is reserved for research/automation emphasis.

Live public telemetry and synthetic research must never share the same provenance styling. `LIVE PUBLIC FEED` uses blue/green semantics. `SYNTHETIC RESEARCH` uses violet/amber semantics.

## Typography

Display and structural labels use IBM Plex Sans Condensed where available. Body copy uses Inter/system sans. All prices, probabilities, sequences, timestamps, latencies and P&L values use the mono data stack with tabular numerals.

Uppercase is permitted for compact system labels and provenance tags, but not for explanatory copy or errors. Numeric hierarchy should come from size/weight, not glow.

## Layout

Desktop and tablet use a command-terminal grid rather than equal-size cards. The shell has: top command/navigation bar, compact live market strip, main analytical matrix, the Expiry / Risk Field anchor, and a lower execution/portfolio band.

Critical live state remains above the fold: symbol, price, spread, feed freshness, transport, latency/age, sequence generation, system mode and kill-switch state. Research panels may collapse into an explicit unavailable state without disturbing live telemetry.

On narrow screens, preserve reading order and provenance. Panels become a single column; controls remain reachable; no horizontal clipping of primary controls. Scroll ownership belongs to the document unless a panel explicitly owns a data surface.

## Elevation & Depth

Depth is created with contrast, border hierarchy and restrained inner highlights. Avoid blurred translucent layers. Focused or active operational panels may receive a subtle violet or blue edge, never a large glow.

## Shapes

Panels use 10px radii; controls 7px; source/status tags are pills. Dense tables and data strips stay mostly square internally. Avoid mixing many radius families.

## Components

`frame` is the canonical panel surface. `sourceTag` is the canonical provenance primitive. Action controls use semantic native buttons with visible focus states. Market symbol selectors use compact segmented buttons.

Automation is explicitly **simulation/paper/replay only**. Any pipeline representation must end in `Execution Simulator` / `Paper Execution`; it must never imply brokerage, exchange-account connectivity or real-money order routing.

The terminal should expose four interaction states whenever applicable: default, hover/focus, pending/reconciling, and unavailable/error. Disabled research surfaces remain legible and explain why they are unavailable.

## Do's and Don'ts

Do keep information density high while grouping by decision context. Do expose source provenance directly in JSX. Do reserve the most expressive visual treatment for Expiry / Risk Field and Automation. Do use stable geometry during loading/reconciliation.

Don't invent performance data, strategy fills or balances. Don't populate disabled research with synthetic-looking live values merely to make the screen appear active. Don't label tick-grouped micro candles as true time-bucket OHLC. Don't hide failure states. Don't introduce real-money execution affordances.