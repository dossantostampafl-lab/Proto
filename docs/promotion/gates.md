# Promotion gates (Phase 6)

This is Phase 6 of `docs/plans/2026-10-13-tradingview-agent-trading.md`. Each *scope* is
promoted on its own. A scope is an account, a venue, a strategy, a model version and a credential
set:

```text
RESEARCH → REPLAY → SHADOW → PAPER → CANARY → LIVE
```

A scope advances **one stage at a time**, and only with the evidence the next stage requires.
**Automation can only demote.**

## What each stage needs

| To enter | Needs |
|---|---|
| REPLAY | nothing |
| SHADOW | `REPLAY_REPRODUCIBLE`: a deterministic, point-in-time replay with a reproducible result |
| PAPER | `SHADOW_COMPARISON`: real signals, orders only recorded, compared with the market |
| CANARY | An authenticated operator, **7 clean days in PAPER**, and passing `ADVERSARIAL_SUITE`, `RECOVERY_DRILL`, `ROLLBACK_DRILL` and `WATCHDOG_ACTIVE` evidence |
| LIVE | Not certifiable yet |

- **Where the CANARY evidence comes from (Phase 6b).** The three drills are run by the API itself
  (`/v1/promotion/evidence/drill`), and `WATCHDOG_ACTIVE` passes only after 24 h of continuous
  watchdog heartbeats (`/v1/promotion/evidence/watchdog`).
- **When evidence counts.** It must be recorded *while in the current stage* and be at most
  14 days old. If the latest evidence of a kind failed, the gate is closed. The reference (CI run,
  commit, drill id) is where a reviewer reproduces it.
- **Clean days.** They count from the later of two moments: entry into PAPER, or the last
  violation.
- **Operator.** CANARY needs an operator. It is refused unless `OPERATOR_API_TOKEN` is configured,
  because only then has the request been authenticated.

## Canary (owner decisions)

- **Capital.** A CANARY intent is sized on at most **R$500** (`CANARY_CAPITAL`). The 0.25% per
  position, 1% daily-loss and 5% drawdown limits still apply on top.
- **Paper minimum.** At least **1 week** in PAPER without violations.
- **Certification check.** The Risk Engine approves a CANARY intent only when its scope is
  certified at CANARY. LIVE intents are always refused.
- **No real money yet.** The executor still runs on the testnet only, and it refuses CANARY and
  LIVE intents. Turning on a real-money canary is a separate, explicit step: a live executor
  scoped to one certified scope, with the Ed25519 permits below.

## Automatic demotion

Each of these events is a *violation* for every scope on that venue:

- a reconciliation divergence;
- a latched daily-loss or drawdown lockdown.

A violation resets the clean period, demotes CANARY to PAPER (and LIVE to CANARY), and is journaled
as `PROMOTION_VIOLATION`.

## Ed25519 permits

With `RISK_PERMIT_PRIVATE_KEY` set, the Risk Engine signs permits with Ed25519. The executor gets
only `EXECUTOR_PERMIT_PUBLIC_KEY`, so it can verify permits but never mint one. Generate a key pair
with:

```bash
python -m services.risk.permit_keys
```

HMAC permits still work for in-process paper. An executor configured with a public key rejects
them.

## API

| Route | |
|---|---|
| `GET /v1/promotion/policy` | Stages, required evidence, canary capital, paper minimum |
| `GET /v1/promotion/scopes` | Every scope with stage, evidence, violations and history |
| `POST /v1/promotion/evidence` | Operator. `{scope, kind, reference, passed}` |
| `POST /v1/promotion/promote` | Operator. `{scope, target}`; 409 with every reason when refused |
| `POST /v1/promotion/demote` | Operator. `{scope, reason}`; never refused |
| `POST /v1/promotion/evidence/drill` | Operator. `{scope, kind, seed}`: runs the drill and records its outcome (Phase 6b, `docs/drills/drills.md`) |
| `POST /v1/promotion/evidence/watchdog` | Operator. `{scope}`: records `WATCHDOG_ACTIVE` from the watchdog's measured coverage (`docs/watchdog/watchdog.md`) |

Scopes persist in `promotion_scopes` (migration `0010_promotion_scopes`).
