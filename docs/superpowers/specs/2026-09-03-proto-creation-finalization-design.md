# PROTO + The Creation Finalization Design

## Goal
Deliver a final, auditable safe-scope release where The Creation is the creator-facing command surface and PROTO is the quantitative execution/research engine for LIVE public monitoring, Research, Replay, PAPER and SHADOW.

## Architecture
- The Creation remains a separate product boundary and sends authenticated, idempotent `Mission` payloads to PROTO through `/creation/missions`.
- PROTO validates identity, allowlisted jobs, runtime mode and safety constraints before enqueuing work into ProtoBrain.
- ProtoBrain dispatches only registered safe jobs. Risk Engine remains authoritative for PAPER/SHADOW admission.
- Results are persisted through Decision Memory and operational audit surfaces and returned as mission receipts.
- The PROTO dashboard stays the technical terminal; The Creation becomes the primary creator-facing command center once its repository is accessible.

## PROTO final dashboard
The active PROTO UI must expose fact-only views for:
- live BTC/ETH/SOL public read-only telemetry;
- Instrument Universe with explicit `LIVE READ-ONLY` versus `CATALOG ONLY` coverage;
- PAPER controls, autopilot and required stop-loss configuration;
- SHADOW start/stop and hypothetical evaluation semantics;
- Risk Engine / kill-switch state;
- Decision Memory status and recent decisions;
- The Creation bridge status;
- orchestration readiness and deployment provenance.

No UI element may imply equity live coverage, broker connectivity, portfolio mutation or real-money execution when those facts are not proven.

## Multiasset boundary
- `InstrumentRegistry` is the canonical provider-neutral universe.
- Crypto BTC/ETH/SOL remain proven public-live instruments.
- US and B3 instruments may be catalogued only from explicit configuration allowlists.
- Read-only provider adapters may expose current market-data observations when configured and validated.
- Catalog membership alone never implies a live feed.

## The Creation bridge
- Disabled unless a deployment secret is configured.
- Requires `THE_CREATION` origin and an authenticated shared-secret boundary.
- Accepts only allowlisted safe jobs/modes.
- Enforces idempotency and durable mission receipts.
- Must never provide a path that bypasses Risk Engine or enables financial connectivity.

## Safety invariants
- `financial_connectivity=false`.
- `real_money_execution=false`.
- PAPER/SHADOW only for autonomous trading behavior.
- SHADOW never mutates portfolio or persists fills.
- PAPER routes through authoritative simulation/risk gates.
- Kill switch and stale-data fail-closed rules remain authoritative.

## Release criteria
1. PR gates for Python, Rust, web, security, Graphify, live-release and mutation-safety pass.
2. Production deploy provenance matches the exact merged git SHA and UI source digest.
3. Production Browser/UI/Paper/Orchestration/Event Journal contracts pass against the public Railway origin.
4. A bounded PAPER autonomy soak validates runtime stability, feed freshness, risk rejections, stop-loss behavior, watchdog behavior and truthful P&L/position outputs.
5. No release is labeled final while production serves a stale revision or while a required external dependency is unavailable.

## The Creation repository
Once `dossantostampafl-lab/the-creation-os` becomes accessible to the GitHub connector, its UI will be audited and redesigned as a Creator Command Center. It will show mission status, opportunities, attention-required decisions, recent results and PROTO health while keeping detailed quant surfaces inside PROTO.
