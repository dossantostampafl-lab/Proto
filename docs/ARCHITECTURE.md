# Architecture — Proto Simulation Engine

## Scope

Proto is a research and educational simulation platform for market data, probabilistic models and paper-trading experiments. The MVP does not place real financial orders and does not contain live execution credentials.

## Core components

- `apps/web`: research dashboard and simulation monitoring UI.
- `apps/api`: Python API, domain contracts and experiment orchestration.
- `crates/engine`: deterministic Rust primitives for simulation checks and accounting logic.
- PostgreSQL: local persistence target for experiments, snapshots and audit records.
- GitHub Actions: automated linting and tests.

## Simulation flow

Market snapshot -> research model -> proposed simulated order -> validation -> paper fill -> simulated position/P&L -> audit event.

## Engineering principles

- Simulation-first design.
- Deterministic, testable domain logic.
- Explicit versioned contracts.
- Validation before simulated fills.
- Observability and auditability from the start.
- No real-money execution in the MVP.

## Development phases

1. Foundation: API, contracts, simulator, Rust core, web shell, containers and CI.
2. Market-data research: normalized snapshots, candles, order-book models and replay datasets.
3. Quant research: implied probability, fair-probability estimators, volatility, calibration and microstructure features.
4. Simulation accounting: positions, simulated P&L, exposure, limits and scenario testing.
5. Observability: structured logs, metrics, traces, experiment journal and dashboards.
6. Quality: unit, integration, property and replay tests plus dependency/security scanning.

## Quality gate

Each material feature should have an explicit contract, unit tests, adverse-case tests, error handling, observable metrics and concise documentation.
