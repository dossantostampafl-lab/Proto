# PROTO — Third-party source registry

This registry records **status, not claims of installation**. Detailed source-level
SHA-1 and license for imported files: [FinRobot UPSTREAM](finrobot/UPSTREAM.md).

## Imported and byte-verified (A0, staged only)

| Repository | Immutable revision | License | Imported files | Runtime |
|---|---|---|---|---|
| [FinRobot](https://github.com/AI4Finance-Foundation/FinRobot) | `2717499b8e30f242640af08c4ad9afd1113c2d45` | Apache-2.0 | 4 original agent instruction files and original LICENSE in `third_party/finrobot/` | **NOT CONNECTED** |

The four copied Markdown files are original **instructions**, not standalone
agents; the upstream FinRobot agent factory and financial data pipelines have
**not** been copied or executed in PROTO.

## A1 optional runtime adapter (separate branch, NOT deployed)

The original FinRobot Python SDK is referenced in `Dockerfile.research` by
its pinned upstream commit `2717499b8e30f242640af08c4ad9afd1113c2d45`
and installed using the upstream package's `finrobot_desktop/pyproject.toml`.

- PROTO-authored glue: `apps/research_worker/__main__.py`.
- Original upstream implementation: `finrobot/sdk.py`, factory/pipelines and
  compute modules at the pinned commit (not copied into PROTO).
- Execution mode: operator-launched ephemeral container, no API/web/executor
  integration, no activated resident agents or live feeds.
- Validation: `tests/unit/test_finrobot_research_worker.py` (offline SDK stub),
  plus pending optional container smoke checks.
- Data-source and LLM secrets are not supplied by this repository.
- No claim of end-to-end vendor API success or production readiness.

## Accepted for further isolated evaluation, not imported

| Repository | Immutable reference | Purpose | Decision |
|---|---|---|---|
| [TradingAgents](https://github.com/TauricResearch/TradingAgents) | `e8f5503e9b86d5c2710b0c76b1f686703968c9df` | fundamental, news, sentiment, bull/bear, research manager | Read-only isolated research worker; no external trader, risk engine or OMS |
| [Microsoft RD-Agent](https://github.com/microsoft/RD-Agent) | `484776c211e4fbbeef03e0ec00d6bbee7362a4f4` | factor/model research | Offline/sandbox only; deterministic validation |
| [FinGPT](https://github.com/AI4Finance-Foundation/FinGPT) | `fdb04c9a273d1ccc3764b09b8e3ed1e708e57566` | financial sentiment, forex context | Independent audit of dataset/model licenses and PIT data |
| [AI Hedge Fund](https://github.com/virattt/ai-hedge-fund) | `4e1657ddc689e3939c481a8f0955444b10f02dc9` | PEAD research benchmark | Benchmark only until integration maturity verified |
| [Agentic Portfolio Risk](https://github.com/AndrewFSee/agentic_ai_portfolio_risk_manager) | `2e95ed722acb5a7f93223721eb7915991ca4a2d9` | advisory portfolio criticism | Evaluate usefulness vs existing PROTO risk; never change constitutional limits |
| [Hummingbot Condor](https://github.com/hummingbot/condor) | `8e4860fa3d7efd4b55c2b74dd7f1f439e5a4f32c` | attribution/agent session summaries | Reference only |
| [helm-agents](https://github.com/QuantiaAI/helm-agents) | `244225ca75f1d402341ad7c67c2d5fbaf9f148d7` | multi-agent architecture benchmark | Reject copying redundant TypeScript agent stack |

## Mandatory criteria for any subsequent import

1. Confirm original path, immutable revision and repository `LICENSE`/`NOTICE`.
2. Prefer package/isolated upstream execution to a partially copied dependency
   graph. Record exact original Git blob hash for any VENDOR file.
3. Preserve raw upstream files; implement adapters in separate files/commits.
4. Never grant research agents access to execution credentials, money movement,
   RiskPermit issuance, risk-limit configuration, production OMS or broker tools.
5. Validate dependency compatibility (Python 3.13 for core PROTO), dataset
   licenses, source freshness and point-in-time availability.
6. Gate any model's conclusions on external evidence. Missing provenance =>
   `ABSTAIN`, never synthetic claims or order authorizations.
7. Keep existing `services/risk`, `services/oms`, `services/agents`,
   `services/orchestration` and the live monitor as the sole canonical systems.
8. Use feature branches and PRs; no merge with red required CI/security.

## Claude Trading Skills (advisory skills, Claude Code only)

| Repository | Immutable revision | License | Imported files | Runtime |
|---|---|---|---|---|
| [claude-trading-skills](https://github.com/tradermonty/claude-trading-skills) | `c9b84196a44a56a553293114fdae1f30bdd08c58` | MIT ([copy](claude-trading-skills.LICENSE)) | 13 skill folders in `.claude/skills/` | **NOT CONNECTED** (assistant guidance only) |

Vendored skills: `backtest-expert`, `crypto-regime-analyzer`, `data-quality-checker`,
`drawdown-circuit-breaker`, `edge-strategy-designer`, `edge-strategy-reviewer`,
`market-environment-analysis`, `position-sizer`, `pre-trade-discipline-gate`,
`residual-edge-analyzer`, `scenario-analyzer`, `trade-hypothesis-ideator`,
`trade-performance-coach`.

- **Selection rule:** only skills that need no API key and no broker connection. The
  only network access is `crypto-regime-analyzer`'s public, keyless GETs (CoinGecko,
  Binance futures market data).
- **Excluded:** every skill that talks to a broker or needs credentials. That covers
  Alpaca (`portfolio-manager`, `parabolic-short-trade-planner`), FMP, FINVIZ,
  `mt5-robot-tester` and the launchd automation.
- **Only local change:** script paths in the Markdown were rewritten from
  `skills/<name>/` to `.claude/skills/<name>/`. Code is byte-identical to upstream.
- **Boundary:** these skills help the operator and Claude Code reason about research and
  risk. Nothing in `apps/` or `services/` imports them, they emit no intents and place no
  orders, and their outputs (`reports/`, `state/`) are gitignored. The `AGENTS.md`
  limits, the Risk Permit and the executor boundary are unchanged.
- **Not run in PROTO:** the vendored scripts and their upstream tests were not executed
  during intake.
