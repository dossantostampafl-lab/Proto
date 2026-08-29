# Agent Workstreams

The project is organized into coordinated engineering workstreams. They are roles for planning, review and execution; integration is centralized through pull requests and CI.

## Architecture
Owns boundaries, ADRs, contracts and dependency direction.

## Quant / Python
Owns research models, probability estimation, calibration, feature engineering and APIs.

## Rust Engine
Owns deterministic low-latency primitives, sequencing and simulation-critical logic.

## Risk / Simulation
Owns limits, simulated fills, exposure, scenario tests and accounting invariants.

## Frontend
Owns the research terminal, visualization and operator UX.

## Infra / CI
Owns containers, development environment, reproducible builds and automation.

## Security
Owns dependency hygiene, secrets prevention, least privilege and audit controls.

## QA / Audit
Owns test strategy, adversarial cases, regression checks and release gates.

## Integration loop

Plan -> implement -> test -> audit -> correct -> integrate.

No workstream may introduce real-money order execution into the MVP. The supported operating modes are simulation and paper trading only.
