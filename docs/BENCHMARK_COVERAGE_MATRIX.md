# Benchmark coverage and adoption matrix

This matrix records benchmark-derived capabilities adopted into Proto's native simulation, paper-trading and historical-replay architecture. The goal is capability coverage and engineering rigor, not cloning external systems.

Status legend: `DONE` = implemented in Proto's active path; `DEFER` = useful follow-on work but not research-RC critical; `REJECT` = deliberately excluded from the current safety/licensing boundary.

## TemiKayode/parallax — MIT

| Capability | Proto status | Decision | Evidence / notes |
|---|---|---|---|
| Fail-closed/veto-only risk gate | DONE | Keep | Rust risk path blocks on hard limits, reconciliation and kill-switch state. |
| Working-order reservations | DONE | Keep | Pending orders consume market/asset/correlated capacity. |
| Atomic cumulative batch risk | DONE | Keep | Batch candidates use staged reservations and fail atomically. |
| Reconciliation-before-risk readiness | DONE | Keep | Stateful gate refuses risk until reconciled. |
| Correlated/cluster exposure | DONE | Keep | Correlated exposure limits and cluster controls are active. |
| Session loss/drawdown rails | DONE | Keep | Drawdown limits and kill-switch semantics are active. |
| Execution price collar | DONE | Keep | Explicit execution-side price-collar validation was added. |
| IOC/FOK/marketable-limit semantics | DONE | Keep | Simulator covers GTC/IOC/FOK, partial fills and marketable limits. |
| Deterministic price-time / queue realism | DONE | Keep | L2 queue-ahead/depletion model is implemented in Rust. |
| Configurable latency/fee friction | DONE | Keep | Latency, fee, tick-grid and impact configuration are explicit. |
| Idempotent ambiguous-submit recovery | DEFER | Defer | Persistence/idempotency exist; live venue resend logic is irrelevant without live transmission. |
| Venue deadman/cancel-all | REJECT | Exclude | No real-money venue execution. |
| Live Kalshi/Polymarket adapters | REJECT | Exclude | Prediction-market execution connectivity is outside the current boundary. |

## spencerfletcher/market-maker — MIT

| Capability | Proto status | Decision | Evidence / notes |
|---|---|---|---|
| Exact Decimal money/grid arithmetic | DONE | Keep | Active `PaperPortfolio` uses Decimal; execution fill-grid/notional/fee arithmetic is Decimal internally. |
| Feed health ladder | DONE | Keep | HEALTHY/DEGRADED/STALE/DARK/RECOVERING states are implemented. |
| Feed recovery probation | DONE | Keep | Multiple clean updates are required before full healthy risk state is restored. |
| Captured/public wire golden fixtures | DONE | Keep | Frozen sanitized official public-feed sample is parsed offline with provenance. |
| Mutation testing of safety fixes | DONE | Keep | Targeted `mutmut` CI gate protects reconciliation safety invariants. |
| Queue attribution / fill realism | DONE | Keep | L2 queue-position model is implemented. |
| Exact fee equations / rounding grids | DONE | Keep | Decimal fee/notional calculation and adverse side-aware tick rounding are active. |
| Pre-transaction OS-level fsync discipline | DEFER | Defer | Journaling/persistence exist; full live-order durability discipline is not RC-critical in simulation. |
| Strict teardown around live orders | REJECT | Exclude | No real-money venue execution. |

## nautechsystems/nautilus_trader — LGPL-3.0

| Capability | Proto status | Decision | Evidence / notes |
|---|---|---|---|
| Event-driven lifecycle separation | DONE | Keep | Explicit event phases and services/engines boundaries. |
| Deterministic replay abstractions | DONE | Keep | Supplied clock, ordered events, fingerprint and anti-lookahead. |
| Portfolio/execution/replay integration | DONE | Keep | API replay uses deterministic core/replay clock; portfolio and SQL journal are isolated across timeline changes. |
| Strategy/model registry lifecycle | DEFER | Defer | Useful at larger research scale, not required for current RC. |
| Full adapter ecosystem | REJECT | Exclude | Unnecessary connectivity and LGPL implementation reuse are excluded. |
| Direct LGPL code reuse/linkage | REJECT | Exclude | Architectural reference only unless a future explicit license-compliance decision is made. |

## zostaff/hft-pm — benchmark reference

| Capability | Proto status | Decision | Evidence / notes |
|---|---|---|---|
| OFI | DONE | Keep | Temporal and normalized OFI implemented. |
| Microprice | DONE | Keep | Microprice and deviation features implemented. |
| VPIN/toxicity | DONE | Keep | Rolling volume-bucket VPIN implemented as research feature. |
| Hawkes intensity | DONE | Keep | Hawkes state is integrated into quant pipeline. |
| Hawkes parameter MLE | DEFER | Defer | More robust parameter-estimation research can follow RC. |
| L2 queue tracking | DONE | Keep | Deterministic queue-ahead/depletion fill model implemented. |
| Injectable latency | DONE | Keep | Explicit latency friction is configurable in execution simulation. |
| Purged CV / embargo | DONE | Keep | Validation Engine supports purge and embargo. |
| DSR | DONE | Keep | Implemented. |
| PBO | DONE | Keep | CSCV-style PBO implemented. |
| CPCV | DONE | Keep | Explicit combinatorial purged CV implemented. |
| Delay-injection validation | DONE | Keep | Negative-control delay experiments implemented. |
| Timestamp-shuffle validation | DONE | Keep | Temporal leakage negative control implemented. |
| Diebold-Mariano + Newey-West HAC | DEFER | Defer | Useful for later forecast-comparison research. |
| Avellaneda-Stoikov / GLT strategies | DEFER | Defer | Strategy research, not engine-correctness requirement. |
| Live prediction-market execution runner | REJECT | Exclude | Outside current safety boundary. |

## Other backtesting / quant references

| Capability | Proto status | Decision | Evidence / notes |
|---|---|---|---|
| Walk-forward evaluation | DONE | Keep | Validation Engine. |
| Parameter stability | DONE | Keep | Plateau/stability diagnostics implemented. |
| Regime robustness | DONE | Keep | Implemented. |
| Block-bootstrap Monte Carlo | DONE | Keep | Deterministic bootstrap implemented. |
| Anti-lookahead replay | DONE | Keep | Visibility boundary and replay-clock integration implemented. |
| Deterministic fingerprints | DONE | Keep | Replay fingerprint implemented. |
| Slippage/market-impact stress | DONE | Keep | Latency, spread and depth/participation-sensitive impact are active. |
| Strategy registry / experiment catalog | DEFER | Defer | Can follow after the current research RC. |

## Completed high-value adoption backlog

All eleven approved benchmark-derived gaps are now implemented and covered by repository gates:

1. **Exact active-path accounting** — Decimal `PaperPortfolio`, fees, turnover, exposure and P&L.
2. **L2 queue-position fill model** — queue ahead, cancellation/depletion and partial fills.
3. **Explicit execution friction** — latency, fees, adverse tick-grid rounding and impact parameters.
4. **Execution price collar** — excessive-price-distance rejection before matching.
5. **VPIN/toxicity** — rolling volume-bucket toxicity feature.
6. **Delay and timestamp-shuffle controls** — adversarial checks for timing dependency and leakage.
7. **CPCV** — combinatorial purged cross-validation.
8. **Public-feed golden fixtures** — offline public wire fixture with provenance.
9. **Targeted mutation testing** — safety mutation gate in CI.
10. **Depth-sensitive impact stress** — nonlinear participation/depth cost model.
11. **Integration audit** — deterministic replay core is wired to API replay clock; portfolio state and persistent SQL journal sessions are isolated on replay timeline changes.

## Deliberately excluded

- real-money order routing, broker/exchange credentials, wallets, deposits, withdrawals or custody;
- live prediction-market wagering/execution connectors;
- venue deadman/cancel-all infrastructure whose purpose is real order transmission;
- wholesale or direct reuse of LGPL implementation code;
- copying entire external strategy stacks where Proto has native equivalents or where the strategy is not RC-critical.

## Benchmark-complete decision

For the current **research/simulation/paper/replay scope**, the approved benchmark adoption backlog is complete: there are no remaining `ADOPT` items. `DEFER` items are documented research extensions, and `REJECT` items are intentional safety/licensing exclusions.

Benchmark-complete means software capability coverage only. It does **not** assert financial profitability, durable alpha, or suitability for real-money execution.
