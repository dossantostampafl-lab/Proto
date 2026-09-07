# ADR: Autonomous Real-Money Execution Boundary

- Status: Accepted
- Date: 2026-09-07
- Decision owner: Repository owner

## Context

PROTO currently implements research, simulation, historical replay, shadow/paper automation, and public read-only market monitoring. Its repository contract previously prohibited all authenticated financial connectivity and real-money execution.

The owner has authorized the project to evolve into a fully automated trading system. Removing the old prohibition without preserving the current runtime truth would be unsafe: existing endpoints and tests correctly report that the present implementation cannot execute real-money orders.

## Decision

Real-money trading is an authorized target capability. It will be introduced through independently certified paths rather than by globally enabling a `LIVE` flag.

The authority chain is fixed:

```text
data -> model/strategy -> typed intent -> deterministic risk permit
     -> canonical OMS -> venue adapter -> ledger/reconciliation
```

No strategy, LLM, TradingView webhook, UI action, research process, or third-party framework may bypass this chain or hold venue credentials.

## Constitutional controls

Initial immutable defaults:

| Control | Limit |
|---|---:|
| Reference capital | R$10,000 |
| Risk per position | 0.25% of conservative current equity |
| Daily loss | 1% |
| Maximum drawdown | 5% |
| Gross leverage | 1.0x |
| Withdrawal/transfer permission | prohibited |

Automated systems may operate, abstain, reduce risk, halt, reconcile, recover preclassified technical failures, promote validated challengers, and roll back. They cannot loosen constitutional limits, grant credential scope, disable audit or kill switches, or reactivate a constitutional lockdown.

## Release states

Each account/venue/strategy/model/credential tuple advances independently:

```text
RESEARCH -> REPLAY -> SHADOW -> PAPER -> CANARY -> LIVE
```

Promotion requires reproducible evidence for deterministic replay, point-in-time data integrity, risk and OMS invariants, idempotency, partial-fill and unknown-order recovery, reconciliation, chaos recovery, security, observability, and rollback. Failure returns the path to a safer state automatically.

`real_money_execution=false` remains mandatory for every current or future surface not explicitly bound to a certified `CANARY` or `LIVE` path. Documentation and telemetry must never imply that authorization equals implementation or readiness.

## Credentials and money movement

- Credentials are isolated by environment, account, and venue.
- Agents, prompts, research sandboxes, frontend code, and logs never receive secrets.
- Trading credentials cannot withdraw, transfer, or change account security.
- Deposits, withdrawals, custody, and treasury automation remain out of scope.

## Consequences

- The P0 audit can design later phases against a real-money target.
- Existing safety tests remain valid until replaced by narrower tests for a certified path.
- Risk Engine, OMS, ledger, reconciliation, audit, and watchdog remain PROTO-owned authorities.
- External frameworks and venue adapters operate behind canonical contracts.
- Live readiness is never a repository-wide label; it is evidence scoped to a concrete operational path.
