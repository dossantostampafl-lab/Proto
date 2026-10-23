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
