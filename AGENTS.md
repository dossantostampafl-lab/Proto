# Proto Agent Operating Guide

## Graphify first

When `graphify-out/graph.json` or `graphify-out/GRAPH_REPORT.md` is available, use the Graphify knowledge graph before broad repository search for architecture, dependencies, impact analysis, and call-flow questions.

Recommended local flow:

```bash
graphify .
graphify query "<architecture question>"
graphify explain "<symbol or concept>"
```

CI publishes `graphify-code-knowledge-graph` with `graph.json` and `GRAPH_REPORT.md` for pull-request revisions.

## Workstreams

Keep implementation separated into architecture, Python, Rust, data, frontend, tests, security, and performance workstreams. Validate cross-workstream changes through CI before merge.

## Financial execution boundary

The current released runtime remains public read-only BTC/ETH/SOL monitoring plus simulation, replay, shadow, and paper trading until a real-money path passes its release gates. Existing `financial_connectivity=false` and `real_money_execution=false` assertions must remain truthful for every surface that has not been certified for live execution.

Real-money trading development is authorized, including authenticated broker/exchange connectivity and fully automated order execution, only behind the canonical PROTO control plane. A live path must satisfy all of the following before it can report or operate as live:

- strategies and agents produce typed intents; they never call venues directly;
- an independent deterministic Risk Engine issues a short-lived Risk Permit for every order;
- the canonical OMS owns order state, idempotency, retries, and `UNKNOWN` resolution;
- a transactional ledger and continuous reconciliation own positions, balances, fills, fees, and reservations;
- credentials are isolated by environment and venue, are never exposed to agents or prompts, and cannot withdraw or transfer funds;
- research, training, and generated code run without production credentials and cannot modify constitutional controls;
- shadow, paper, adversarial, recovery, and minimal-capital canary gates pass with reproducible evidence;
- kill switches, stale-data guards, drawdown controls, audit lineage, rollback, and independent watchdogs are active;
- the API and UI identify live, delayed, replay, paper, simulated, stale, unavailable, and unknown data truthfully;
- live enablement is scoped per account, venue, strategy, model version, and credential set and fails closed outside that scope.

The constitutional defaults are reference capital R$10,000, maximum risk per position 0.25% of conservative current equity, maximum daily loss 1%, maximum drawdown 5%, and maximum gross leverage 1.0x. Automated components may reduce risk, abstain, halt, reconcile, recover approved failure classes, promote validated challengers, or roll back. They may not raise these limits, enable withdrawals, bypass permits, disable audit, or reactivate a constitutional lockdown.

Deposits, withdrawals, custody, fund transfers, and autonomous changes to constitutional limits remain outside the system. Prediction-market integrations require separate legal, venue, settlement, and jurisdiction gates before activation; no implementation may silently treat them as equivalent to spot or securities execution.

## Git workflow

- Never write directly to `main`.
- Use feature branches and pull requests.
- Do not merge with failing required CI/security gates.
- Prefer deterministic tests and offline fixtures in CI.
- Never commit secrets or generated Graphify output unless explicitly required; CI artifacts are the default distribution mechanism.

## Validation loop

PLAN -> IMPLEMENT -> TEST -> AUDIT -> FIND DEFECTS -> FIX -> RETEST -> RE-AUDIT.
