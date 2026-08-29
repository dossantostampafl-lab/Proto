# LIVE_MONITORING Phase Gate

This document defines the completion gate for the public read-only BTC/ETH/SOL monitoring phase.

## Scope

The phase covers only public market observation and generic platform engineering. It does not introduce account access, credentials, order routing, custody, deposits, withdrawals, leverage or real-money execution.

Hard invariants:

- `financial_connectivity=false`;
- `real_money_execution=false`;
- public unauthenticated market-data input only;
- HTTP live surface remains read-only.

## Completion criteria

The phase is complete only when all of the following are true on `main`:

1. The public WebSocket endpoint is canonical, TLS-only and allowlisted.
2. Public frames have bounded size/cardinality and deterministic parser validation.
3. Canonical ticks pass finite-number, price/spread/size, timestamp and freshness quality gates before acceptance.
4. Reconnects create a new connection generation and clear old accepted live state.
5. Source timestamps and Proto receive timestamps remain separate and observable.
6. Readiness is fail-closed for stopped/disconnected/stale/degraded/incomplete/current-generation failures.
7. BTC, ETH and SOL require both source freshness and server-receipt freshness.
8. Per-symbol source sequences are strictly increasing within a connection generation; duplicates and regressions are rejected before snapshots, history or WebSocket fanout.
9. Duplicate/regression rejections are observable per symbol and for the current connection generation.
10. `/live/*` responses are non-cacheable and the live Prometheus surface exposes explicit zero-valued financial-connectivity and real-money-execution gauges.
11. Runtime, HTTP presentation, Prometheus rendering, parser, transport and coverage/readiness logic remain independently testable without external network access.
12. Static isolation tests prohibit live modules from importing financial/execution engines or reading credentials.
13. CI, Security and Graphify are green for the final phase-closing pull request.

## Sequence-integrity telemetry

`GET /live/status` exposes:

- `last_sequence_by_symbol`;
- `sequence_rejections_current_connection`;
- `sequence_rejections_by_symbol`, split into `duplicate`, `regression` and `total`.

The rejection telemetry resets when a new public-feed connection generation is accepted. Global runtime counters remain available separately through the existing application metrics path.

`GET /live/metrics/prometheus` exposes the current-generation sequence state as gauges, including per-symbol duplicate and regression rejection counts. These values are validated before rendering so non-finite telemetry is never emitted.

## Exit rule

The phase exits only after the phase-closing pull request has passed CI, Security and Graphify and has been merged into `main`. Subsequent work should start from the resulting `main` commit on a new branch.
