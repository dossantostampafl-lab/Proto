# Research Council — staged implementation plan (approved scope)

**Baseline:** `main@ca17bc90e0ce7efdb0406ee49a15e4688dccbdf0`, 2026-10-23.

**Purpose:** Add verifiable external financial research capabilities without
duplicating PROTO canonical trading, risk, or quantitative engines. This is an
implementation sequence, **not evidence that all stages are delivered**.

## A0 — Upstream origin + license + integrity (this PR)

- Import **unmodified** original FinRobot instructions for data, financial
  modeling explanation, synthesis, and reports; preserve original license.
- Pin original Git SHAs and verify Git blob content in an offline pytest gate.
- Register future upstreams without falsely labeling them installed.
- Avoid runtime imports, external endpoints and execution or dashboard changes.

**Acceptance:** original hashes match at the target branch; CI/security results
are evaluated; PR #329 and execution components untouched.

## A1 — Research-only isolated worker (future PR)

- Study the **original** FinRobot
  `finrobot_desktop/finrobot/engine/agents/factory.py`, package dependencies
  and input normalization pipeline as a complete unit. Do not copy
  `factory.py` alone: it imports many internal modules.
- Run in separate restricted container/environment and only expose read-only
  evidence and task results to existing PROTO `services/orchestration`.
- No source-adjacent execution credentials, network access to private executor,
  broker API, Docker socket, host filesystem or shared production mounts.
- Check Python versions/LLM costs and add test fixtures with offline data.
- Fail closed: `ABSTAIN` for missing/late evidence, timeout and errors.

## A2 — Earnings, fundamentals, valuation (future PRs)

- Use actual FinRobot deterministic original operators
  `compute/operators/{earnings.py,dcf.py,ddm.py,multiples.py}` with their
  dependency tree, validated numerical inputs, currency and reporting period.
- Add only new capability; existing PROTO analytics/validation are authoritative.
- Record source, filing timestamp, public availability and input digest.

## A3 — News, macro, sentiment (future PRs)

- Original TradingAgents analyst modules under `tradingagents/agents/analysts/`
  in isolated read-only worker. Respect dependencies, data permissions and PIT.
- No assumptions of historical news/social availability.

## A4 — Adversarial research (future PR)

- Original TradingAgents `bull_researcher.py`, `bear_researcher.py`,
  `research_manager.py` as research-only components.
- Disable mandatory BUY/SELL semantics; conflicting/insufficient evidence
  results in `ABSTAIN`. No new risk authority.

## A5 — Factor/model research (future PR)

- Original RD-Agent factor and model loops run in sandbox only; frozen
  datasets and reproducible backtests; validation by PROTO's CPCV/PBO/DSR,
  holdout and model promotion gates.

## A6 — Portfolio research & optional agents (future PR)

- Assess overlap with existing PROTO portfolio and risk, before adopting
  FinRL-X, skfolio or third-party critical commentary. Output research
  suggestions only; canonical risk limits cannot be loosened.

## A7 — Dedicated research UI (future PR)

- React/Vite on-demand Council views from the existing dashboard, with
  provenance, abstention and unavailable states. No Next.js sidecar UI.
- Remain independent from open PR #329 (Floor Control).

## A8 — Homologation and release (future PR)

- Complete Ruff, pytest, web typecheck/build/contract/bundle, security,
  architecture/Graphify and offline research safety tests.
- Require approval and scope-specific release gates before any live rollout.
- Research readiness does **not** imply live trading readiness or profitability.

See `AGENTS.md`, `CLAUDE.md`, existing PROTO architecture and
`third_party/UPSTREAM_MANIFEST.md`; those constraints override upstream prompts.
