# CLAUDE.md

Guide for Claude Code in this repository. **`AGENTS.md` is binding** and wins on any conflict: it
holds the financial execution boundary, the constitutional limits and the git workflow.

## What Proto is

Proto automates trading behind one control plane. TradingView alerts and PROTO signals feed hybrid
agents (deterministic decisions, AI reviewer that may only shrink). A deterministic Risk Engine
issues permits, and a canonical OMS, ledger and reconciliation execute.

- **Today:** paper and Binance Spot **testnet** only.
- **Real money:** none until a path passes the release gates.
- **Plan:** `docs/plans/2026-10-13-tradingview-agent-trading.md` (phases 0–7).

```text
TradingView ─> signals ─> agents (fusion + reviewer) ─> Risk Engine ─(HMAC permit ≤60 s, single use)─>
OMS ─> paper venue | isolated executor (Binance testnet) ─> ledger ─> reconciliation ─(divergence)─> kill switch
```

## Non-negotiables (from AGENTS.md)

- **Order authority.** Nothing reaches a venue without a Risk Permit consumed by the OMS. Agents
  and strategies produce intents only.
- **Limits.** Reference capital R$10,000; 0.25% risk per position; 1% daily loss; 5% drawdown;
  1.0x gross leverage.
  - Code may tighten these limits, abstain or halt.
  - It never raises a limit, enables withdrawals or bypasses a permit.
- **Truthful labels.** `financial_connectivity` and `real_money_execution` stay `false`
  everywhere until a live scope is certified. Every view says whether its data is live, paper,
  testnet, stale or unavailable, and never shows invented numbers.
- **Credentials.** They live only in the isolated executor (`apps/executor`,
  `services/venues/credentials.py`).
  - Never import `services.venues` or `apps.executor` from `apps/api`; this is enforced by
    `tests/unit/test_venue_isolation.py`.
  - Never commit secrets.
- **Git.** Never write to `main`. Use feature branches and PRs, and don't merge with red CI.

## Layout

| Path | What |
|---|---|
| `apps/api/app/` | FastAPI app (`main:app`). Phase surfaces: `signal_surface`, `agent_surface`, `risk_surface`, `oms_surface`, `floor_surface`, `executor_client` |
| `apps/executor/` | Isolated executor (testnet only): OMS + ledger + Binance adapter behind a private bearer-token API |
| `apps/web/` | React + Vite dashboard. `src/views/Floor.tsx` + `floorScene.tsx` is the live trading floor |
| `services/execution/` | Contracts (intents, permits, reports, ledger entries), constitution, paper venue |
| `services/agents/` | Fusion, sizing, shrink-only reviewer |
| `services/risk/` | Risk Engine, governor (kill switches, lockdowns), permits |
| `services/oms/` | Order manager, transactional ledger, reconciliation, durable store |
| `services/venues/` | Binance Spot adapter and credential loading (executor only) |
| `services/promotion/` | Promotion gates per scope, canary policy, automatic demotion |
| `services/watchdog/`, `apps/watchdog/` | Independent watchdog: probes API and executor, halt-only tokens, never releases |
| `services/drills/` | Adversarial, recovery and rollback drills with reproducible digests (`python -m services.drills`) |
| `migrations/` | Alembic. New tables must be registered in `migrations/env.py` and created in `init_database` |
| `docs/` | Per-phase docs: `tradingview/`, `agents/`, `risk/`, `oms/`, `venues/`, `executor/`, `dashboard/`, `promotion/`, `watchdog/`, `drills/`, `runbooks/`, `adr/` |

## Commands

```bash
# Python (3.13). CI runs exactly these two.
pip install -e '.[dev]'
ruff check apps services tests
pytest                       # asyncio_mode=auto, --maxfail=1

# Web (Node 22). CI runs all four.
cd apps/web && npm ci
npm run typecheck && npm run build && npm run contract-check && npm run bundle-check

# Run locally
uvicorn apps.api.app.main:app --reload       # API + built dashboard on :8000
uvicorn apps.executor.main:app --port 8100   # executor (needs EXECUTOR_* and PROTO_CRED_* env)
```

- **Formatting.** Don't run `ruff format` on whole directories: much existing code is not
  formatted. Format only the files you touch.
- **Contract check.** `apps/web/scripts/contract-check.mjs` enforces the provenance and boundary
  labels and forbids real-money wording in the UI. Keep it passing.
- **Bundle budget.** At most 450 KB per JS file and 700 KB loaded up front (the fallback floor photo
  included). The live 3D floor loads on demand, never preloaded: JS chunks `Floor3DThree-*` (three.js,
  ≤ 600 KB), `Floor3D-*` and `Floor3DAssets-*` (≤ 120 KB each, ≤ 800 KB together), plus its CC0 people
  and textures in `public/floor3d/` (≤ 3 MB, ≤ 1 MB per file; sources and licences in its `ASSETS.md`).
  The stylized building view ("Realista | Prédio" toggle, `src/views/floorBuilding/`) is its own
  on-demand chunk `FloorBuilding-*` (≤ 150 KB, reuses `Floor3DThree-*`), loaded only when selected.
  The mission-control room ("Controle", `src/views/floorControl/`) is a third view in its own chunk
  `FloorControl-*` (≤ 120 KB, no WebGL): the approved mockup picture `public/floorcontrol/room-photo.webp`
  (≤ 700 KB per file, ≤ 1.4 MB together, never preloaded) with live panels and agent tags drawn exactly
  over its sample ones, at its own pixel coordinates; its totals come from `GET /v1/floor/summary`.
  `localStorage` is allowed only in `src/views/floorViewPref.ts`, for that view choice.

## PRAGMA-CORE 2.0

@docs/pragma/PRAGMA-CORE.md

The contract above is imported for efficiency and reporting only; `AGENTS.md` still wins on any
conflict. Project skills `pragma-debug`, `pragma-feature`, `pragma-audit`, `pragma-delivery` and
`pragma-benchmark` live in `.claude/skills/` (mirrored for Codex in `.agents/skills/`); invoke
them only when the task matches. Templates: `docs/pragma/`.

## Skills and plugins

`.claude/settings.json` registers two plugin marketplaces and turns the plugins on for everyone who
opens this repository in Claude Code, including cloud sessions. They install when a session starts.

- **ECC** (`ecc@ecc`, from `affaan-m/everything-claude-code`, MIT): agents, skills, hooks and
  workflows for TDD, code review, security review, verification loops, and backend, frontend and
  Python patterns.
- **Superpowers** (`superpowers@superpowers-marketplace`, from `obra/superpowers`): brainstorming,
  writing-plans, subagent-driven-development, test-driven-development and
  verification-before-completion. The phase plans follow these workflows (see `docs/superpowers/`).

- **Trading skills** (13 folders from `tradermonty/claude-trading-skills`, MIT, pinned in
  `third_party/UPSTREAM_MANIFEST.md`): backtesting, position sizing, drawdown and pre-trade gates,
  regime analysis and edge review in `.claude/skills/`. They are advisory only. Skills that need a
  broker or API keys were left out on purpose.

The `AGENTS.md` boundary applies to every skill. No plugin may weaken the financial boundary, add
credentials or skip CI gates.

## Conventions

- **Tests.** Prefer deterministic tests with offline fakes: `tests/unit/fake_binance.py` for the
  venue, `httpx.MockTransport`, and sqlite (`aiosqlite`) for SQL. Surface tests patch `settings`
  on every module they touch and reset state with the `reset_*_state()` helpers.
- **Decimals.** Store exact text for decimals where SQLite would round them; see
  `services/oms/store.py`.
- **Journal.** Everything that changes state goes to the operational journal
  (`record_operational_event`). The trading floor streams those events.
- **Validation loop.** PLAN → IMPLEMENT → TEST → AUDIT → FIND DEFECTS → FIX → RETEST → RE-AUDIT.
