# PROTO + The Creation Finalization Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Finalize the safe-scope PROTO release, prove the deployed artifact, complete the multiasset read-only surface, and connect The Creation through an authenticated mission bridge when its repository is available.

**Architecture:** PROTO remains the quantitative engine and technical terminal. The Creation remains a separate creator-facing system that submits authenticated, idempotent, allowlisted missions to ProtoBrain. All autonomous market behavior remains PAPER/SHADOW behind authoritative risk gates.

**Tech Stack:** Python 3.13, FastAPI, Pydantic, SQLAlchemy, Rust risk/execution engines, React/TypeScript/Vite, GitHub Actions, Railway.

**Spec:** `docs/superpowers/specs/2026-09-03-proto-creation-finalization-design.md`

## Global Constraints
- `financial_connectivity=false`.
- `real_money_execution=false`.
- Autonomous trading behavior is restricted to PAPER/SHADOW.
- SHADOW never mutates portfolio or persists fills.
- PAPER uses authoritative simulation/risk gates and kill-switch/freshness controls.
- No UI or API may infer live provider coverage from catalog membership.
- Production readiness requires exact git/source provenance, not provider deploy status alone.

---

### Task 1: Finish and merge the dashboard/control-plane PR

**Files:**
- Modify as required: `apps/api/app/universe_surface.py`
- Modify as required: `apps/web/src/autonomy-control-deck.ts`
- Modify as required: `apps/web/scripts/autonomy-control-deck-check.mjs`
- Modify as required: `.github/workflows/production-ui-contract.yml`
- Modify as required: `.github/workflows/production-smoke.yml`

**Interfaces:**
- Consumes: `/universe`, `/shadow/status`, `/shadow/start`, `/shadow/stop`, `/paper/autonomy/status`, `/creation/status`, `/orchestration/status`, `/orchestration/decision-memory/status`, `/orchestration/decision-memory/recent`.
- Produces: deploy-bound operator-terminal-v3 dashboard surface.

- [ ] Inspect every failing PR #253 check and obtain the exact failing assertion/log.
- [ ] Add or adjust a focused contract/test that reproduces each failure.
- [ ] Apply the smallest production fix without weakening safety contracts.
- [ ] Re-run all PR checks until Python, Rust, web, live-release, mutation-safety, Security and Graphify are green.
- [ ] Merge PR #253 only at a stable green head SHA.

### Task 2: Prove the Railway production artifact

**Files:**
- Modify only if a contract defect is found: `.github/workflows/production-*.yml`, `railway.json`, `Dockerfile`, `apps/api/app/railway_app.py`.

**Interfaces:**
- Consumes: `/deployment/info`, response provenance headers, public Railway origin.
- Produces: evidence that the public site serves the exact merged SHA and UI digest.

- [ ] Inspect production workflow runs for the merged main SHA.
- [ ] Verify `X-Proto-Git-Commit-SHA`, `X-Proto-UI-Source-SHA256`, release identities and cache policy.
- [ ] Verify Production UI, Browser E2E, Paper, Orchestration and Event Journal contracts.
- [ ] If production is stale, exhaust repository/configuration fixes that do not require external credentials.
- [ ] Record any remaining Railway credential/source mapping problem as an external blocker only after repository actions are exhausted.

### Task 3: Complete read-only multiasset runtime coverage

**Files:**
- Modify: `apps/api/app/settings.py`
- Modify: `apps/api/app/universe_surface.py`
- Create or modify: `apps/api/app/equity_market_surface.py`
- Modify: `apps/api/app/railway_app.py`
- Modify: `services/market_data/equity_readonly.py`
- Test: `tests/unit/test_multiasset_foundation.py`
- Test: `tests/unit/test_equity_market_surface.py`

**Interfaces:**
- Consumes: `AlpacaEquityReadOnlyProvider.latest_quote(symbol)` and `BrapiEquityReadOnlyProvider.latest_price(symbol)`.
- Produces: bounded read-only HTTP market-data surface with explicit provenance, freshness and no-execution flags.

- [ ] Write failing tests for configured US/B3 read-only market observations, allowlist enforcement, invalid/zero quote rejection and provider failure semantics.
- [ ] Run focused tests and confirm failures.
- [ ] Implement a read-only provider surface that never exposes order/trading methods and never marks catalog-only instruments live.
- [ ] Add session/quote validity guards where bid/ask data is non-executable or stale.
- [ ] Run focused and full Python tests plus ruff.
- [ ] Commit the independently testable multiasset runtime block.

### Task 4: Expand the dashboard from fixed crypto cards to a real Market Explorer

**Files:**
- Modify: `apps/web/src/approved-terminal.tsx`
- Modify: `apps/web/src/approved-terminal.css`
- Modify: `apps/web/src/autonomy-control-deck.ts`
- Modify: `apps/web/scripts/frontend-contract-check.mjs`
- Modify: `apps/web/scripts/autonomy-control-deck-check.mjs`

**Interfaces:**
- Consumes: `/universe` and read-only equity market surface from Task 3.
- Produces: dynamic instrument explorer while retaining BTC/ETH/SOL proven-live core and existing production E2E selectors.

- [ ] Add contract assertions for dynamic Universe rendering and catalog/live distinction.
- [ ] Run web contract check and verify failure before implementation.
- [ ] Implement Market Explorer with progressive disclosure: asset class, venue, symbol, coverage/provenance and freshness.
- [ ] Preserve existing institutional terminal views and production-browser compatibility for the proven crypto core.
- [ ] Run TypeScript typecheck, contract checks, build and bundle budget.
- [ ] Commit the dashboard multiasset block.

### Task 5: Add bounded autonomous PAPER soak verification

**Files:**
- Create: `tests/integration/test_paper_autonomy_soak.py`
- Create or modify: `.github/workflows/paper-autonomy-soak.yml`
- Modify if needed: `apps/api/app/paper_autonomy_bootstrap.py`

**Interfaces:**
- Consumes: live public feed, PAPER autopilot, stop-loss, risk decisions and watchdog metrics.
- Produces: bounded soak report containing factual counters only.

- [ ] Write a bounded soak test with explicit duration/cycle inputs and no invented market events.
- [ ] Verify it records cycles, signals, accepted/rejected submissions, stop-loss exits, feed stale intervals, watchdog restarts/failures and truthful PAPER P&L when available.
- [ ] Verify kill switch, runtime mode changes and stale feed prevent unsafe recovery/submission.
- [ ] Run the bounded soak in CI or a production-safe contract environment.
- [ ] Persist the report as a CI artifact without asserting profitability.

### Task 6: Audit and redesign The Creation when the repository becomes accessible

**Files:**
- Target repository: `dossantostampafl-lab/the-creation-os`.
- Exact paths determined only after repository inspection.

**Interfaces:**
- Consumes: existing DEUS/SOPHIA/ROCKMAM/Inception/Central Core/Tree Core/Universes architecture and PROTO `/creation/*` contract.
- Produces: Creator Command Center and authenticated mission client.

- [ ] Inspect repository structure, current frontend/backend entrypoints and recent CI before modifying code.
- [ ] Preserve existing cognitive architecture while simplifying the creator-facing surface.
- [ ] Implement command/conversation-first home, active missions, opportunities, attention-required decisions, recent results and PROTO health.
- [ ] Implement authenticated mission submission, receipt tracking, retry/idempotency and explicit unavailable/degraded states.
- [ ] Keep detailed Hawkes/Greeks/microstructure/risk internals in PROTO rather than duplicating them in The Creation.
- [ ] Add unit, integration and browser contracts for the bridge and dashboard.

### Task 7: Final release audit

**Files:**
- Update release documentation only after evidence exists.

**Interfaces:**
- Consumes: CI results, production contracts, deployment provenance and soak artifact.
- Produces: final release decision for safe scope.

- [ ] Confirm no required check is failing or pending.
- [ ] Confirm production serves the exact approved revision.
- [ ] Confirm public-live BTC/ETH/SOL, PAPER, SHADOW, Risk, Decision Memory, Event Journal and Creation status are truthful and reachable.
- [ ] Confirm `financial_connectivity=false` and `real_money_execution=false` everywhere in the safe release surface.
- [ ] Confirm external blockers are documented separately and are not mislabeled as completed.
- [ ] Mark the safe-scope release final only after all evidence above is present.
