# Benchmark coverage and adoption matrix

This matrix records which benchmark-derived capabilities are already present in Proto, which are only partially integrated, which should still be adopted, and which should deliberately be excluded. The goal is not to clone external systems; it is to incorporate the highest-value engineering patterns into Proto's own simulation, paper-trading and historical-replay architecture.

Status legend: `DONE` = implemented in Proto's active path; `PARTIAL` = present but not fully integrated; `ADOPT` = high-value remaining work; `DEFER` = useful but not RC-critical; `REJECT` = intentionally excluded.

## TemiKayode/parallax — MIT

| Capability | Proto status | Decision | Notes |
|---|---|---|---|
| Fail-closed/veto-only risk gate | DONE | Keep | Rust risk engine blocks on hard limits and kill-switch state. |
| Working-order reservations | DONE | Keep | Added in risk-hardening so pending orders consume capacity. |
| Atomic cumulative batch risk | DONE | Keep | Batch candidates are evaluated against staged reservations. |
| Reconciliation-before-risk readiness | DONE | Keep | Stateful gate refuses until reconciliation is explicitly marked. |
| Correlated/cluster exposure | DONE | Keep | Proto has correlated exposure limits; direction-aware netting can be refined later. |
| Session loss/drawdown kill-switch rails | DONE | Keep | Existing Rust limits and kill-switch semantics cover this class. |
| Price collar / stale-touch protection | PARTIAL | ADOPT | Feed health and edge penalties exist; explicit execution-side price-collar rejection should be added. |
| IOC/FOK/marketable-limit semantics | DONE | Keep | Added to simulated execution engine. |
| Deterministic price-time matching | PARTIAL | ADOPT | Basic order semantics exist; full L2 queue-position realism is still missing. |
| Queue-position model | MISSING | ADOPT | Needed to avoid front-of-queue fill optimism in replay/paper results. |
| Configurable network latency and fee schedule in fill model | PARTIAL | ADOPT | Cost/latency penalties exist, but execution should require explicit friction config. |
| Idempotent ambiguous-submit recovery | PARTIAL | DEFER | Proto is simulation/paper only; keep concept for persistence/recovery, not live venue resend logic. |
| Out-of-band cancel-all / venue deadman | OUT OF SCOPE | REJECT | Real-money venue execution is outside Proto's current safety boundary. |
| Live Kalshi/Polymarket adapters | OUT OF SCOPE | REJECT | Prediction-market execution/connectivity remains isolated from live financial services. |

## spencerfletcher/market-maker — MIT

| Capability | Proto status | Decision | Notes |
|---|---|---|---|
| Exact Decimal money/grid arithmetic | PARTIAL | ADOPT | Rust risk and research ledger use exact arithmetic, but API `PaperPortfolio` still uses float internally. |
| Feed health ladder | DONE | Keep | Healthy/degraded/stale/dark/recovering states are implemented. |
| Feed recovery probation | PARTIAL | ADOPT | Recovery state exists; require clean-cycle criteria before restoring full risk. |
| Pre-transaction durable intent / atomic fsync state | PARTIAL | DEFER | Persistence and journals exist; full OS-level fsync discipline is not RC-critical for simulation. |
| Captured real wire fixtures | MISSING | ADOPT | Add sanitized public-feed fixtures for parser and sequencing regression tests. |
| Mutation testing of safety fixes | MISSING | ADOPT | Add targeted mutation/negative-control checks for risk, reconciliation and feed-safety invariants. |
| Queue attribution / fill realism | MISSING | ADOPT | Overlaps L2 queue-position work above. |
| Exact fee equations / rounding grids | PARTIAL | ADOPT | Exact accounting exists in parts; execution fee/grid arithmetic should use Decimal end-to-end. |
| Strict teardown around live orders | OUT OF SCOPE | REJECT | No real-money venue execution. |

## nautechsystems/nautilus_trader — LGPL-3.0

| Capability | Proto status | Decision | Notes |
|---|---|---|---|
| Event-driven lifecycle separation | DONE | Keep | Proto uses explicit event phases and services/engines boundaries. |
| Deterministic replay abstractions | DONE | Keep | Replay engine has supplied clock, ordering, fingerprint and anti-lookahead controls. |
| Portfolio/execution abstraction boundaries | PARTIAL | ADOPT | Modules exist, but duplicate portfolio/replay paths still require integration audit. |
| Strategy/model registry lifecycle | MISSING | DEFER | Useful for scale, but not required before the first research RC. |
| Full adapter ecosystem | OUT OF SCOPE | REJECT | Avoid importing/linking LGPL implementation and avoid unnecessary venue connectivity. |
| Code reuse/linkage | NONE | REJECT | Architecture/reference only unless a deliberate LGPL compliance review is performed. |

## zostaff/hft-pm — MIT

| Capability | Proto status | Decision | Notes |
|---|---|---|---|
| OFI | DONE | Keep | Temporal OFI and normalized OFI are implemented. |
| Microprice | DONE | Keep | Microprice and deviation features are implemented. |
| VPIN | MISSING | ADOPT | Add rolling PM-normalized VPIN as a toxicity/liquidity feature, initially research-only. |
| Hawkes intensity | DONE | Keep | Hawkes state is part of the quant pipeline. |
| Hawkes parameter estimation/MLE | PARTIAL | DEFER | Runtime intensity exists; robust calibration/estimation can follow RC. |
| L2 queue tracking in simulator | MISSING | ADOPT | High-priority execution realism gap. |
| Injectable latency model | PARTIAL | ADOPT | Latency penalty exists; execution-time stochastic/deterministic latency model is still needed. |
| Purged CV / embargo | DONE | Keep | Validation Engine includes purged walk-forward and embargo. |
| DSR | DONE | Keep | Implemented. |
| PBO | DONE | Keep | Implemented via CSCV-style matrix evaluation. |
| CPCV | PARTIAL | ADOPT | PBO splitting exists, but explicit combinatorial purged CV report should be added. |
| Delay-injection validation | MISSING | ADOPT | Needed to test whether edge survives realistic signal/execution delay. |
| Timestamp-shuffle validation | MISSING | ADOPT | Needed as a negative control against accidental temporal leakage. |
| Diebold-Mariano + Newey-West HAC | MISSING | DEFER | Valuable for comparing model/strategy forecasts after RC. |
| Avellaneda-Stoikov / GLT quoting strategies | MISSING | DEFER | Useful research strategies, but secondary to core engine correctness. |
| Live Polymarket paper runner | OUT OF SCOPE | REJECT | Public read-only market monitoring is allowed; prediction-market venue execution connectivity is not. |

## Other backtesting / quant references previously reviewed

| Capability | Proto status | Decision | Notes |
|---|---|---|---|
| Walk-forward evaluation | DONE | Keep | Implemented in Validation Engine. |
| Parameter stability | DONE | Keep | Implemented. |
| Regime robustness | DONE | Keep | Implemented. |
| Block-bootstrap Monte Carlo | DONE | Keep | Implemented. |
| Anti-lookahead replay | DONE | Keep | Implemented. |
| Deterministic fingerprints | DONE | Keep | Implemented. |
| Slippage/market-impact stress surfaces | PARTIAL | ADOPT | Current costs are mostly scalar; add depth/participation-sensitive stress models. |
| Strategy registry and experiment metadata | PARTIAL | DEFER | Validation API exists; formal registry can follow after RC. |

## High-value adoption backlog

The following items are approved for implementation because they materially improve realism, safety or research validity without expanding Proto into real-money execution:

1. **Exact active-path accounting** — convert API `PaperPortfolio` internals to `Decimal`, including fees, slippage, turnover, exposure and P&L, while preserving the existing JSON response contract.
2. **L2 queue-position fill model** — model queue ahead, trade-through and partial depletion instead of assuming immediate front-of-queue fills.
3. **Explicit execution friction configuration** — deterministic/injectable latency, fee/grid rounding and market-impact/slippage parameters required by replay/paper runs.
4. **Execution price collar** — reject simulated orders that cross an excessive distance from fresh touch/fair reference under degraded/stale conditions.
5. **VPIN/toxicity feature** — rolling volume-bucket imbalance feature exposed to the research/edge pipeline, not as an automatic trading command.
6. **Delay and timestamp-shuffle validation** — negative-control validation proving apparent edge does not depend on zero latency or temporal leakage.
7. **Combinatorial purged CV** — explicit CPCV folds/report on top of current purge/embargo primitives.
8. **Captured public-feed fixtures** — sanitized real public BTC/ETH/SOL feed messages for parser, ordering, reconnect and sequence regression tests.
9. **Targeted mutation testing** — require safety tests to fail when reconciliation, stale-feed, risk-limit or anti-lookahead guards are deliberately removed.
10. **Depth-sensitive impact stress** — evaluate execution/P&L under spread widening, queue depletion, reduced depth and participation-rate impact.
11. **Integration audit** — remove or bridge duplicate portfolio/replay implementations so the API path actually uses the hardened accounting/replay primitives.

## Deliberately excluded from the adoption backlog

- real-money order routing, broker/exchange credentials, wallets, deposits, withdrawals or custody;
- live prediction-market wagering/execution connectors;
- venue-specific emergency cancel/deadman infrastructure that only makes sense once real order transmission exists;
- wholesale copying of NautilusTrader or other LGPL implementation code;
- copying entire benchmark strategy stacks when Proto already has equivalent or more appropriate native components.

## Definition of benchmark-complete for Proto

Proto can be considered benchmark-complete for the current research scope when every `ADOPT` item above is either `DONE` with CI/Security/Graphify evidence or has been explicitly downgraded/rejected by an ADR with a technical reason. This definition is about software/research capability coverage; it does not assert financial profitability or production suitability for real-money execution.
