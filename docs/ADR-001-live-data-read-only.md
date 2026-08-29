# ADR-001: Public live market data with a hard read-only boundary

**Status:** Accepted

**Date:** 2026-08-29

**Deciders:** Proto maintainers

## Context

Proto needs current public market observations in addition to deterministic historical replay.
The new capability must not turn the research terminal into a trading or custody system. The
boundary therefore excludes order submission, private account APIs, authenticated trading
sessions, deposits, withdrawals, custody, leverage, and any real-money execution.

`LIVE_DATA_READ_ONLY` means that external data may influence analytics and displays, but never
an execution side effect. Existing simulation records must not be presented as exchange fills.

## Decision

Add `LIVE_DATA_READ_ONLY` as a distinct runtime mode with these invariants:

1. Only public `https://` and `wss://` market-data endpoints on explicit exact-host and port
   allowlists may be contacted. Port 443 is the default; a provider-specific TLS port such as
   9443 requires explicit configuration and review.
2. Private exchange, broker, wallet, trading, or order-routing credentials are rejected at
   configuration/startup. Credentials may not appear in endpoint userinfo or query strings.
3. Outbound REST access is limited to `GET`, `HEAD`, and `OPTIONS`. WebSocket clients may send
   only the provider's public subscription/control messages; authenticated channels and
   transaction messages are forbidden.
4. Live frames pass through validation, normalization, freshness checks, bounded buffering,
   deduplication, and sequence-gap detection before publication.
5. Data quality loss is fail-closed: stale, malformed, out-of-order, or disconnected feeds make
   readiness degraded and must not be silently replaced with invented values.
6. `/v1/simulate` and every state-mutating portfolio/fill endpoint are unavailable while the
   runtime is in `LIVE_DATA_READ_ONLY`. Analytics may remain callable, and must identify their
   source mode and observation time.
7. The API continues to report `real_money_execution: false`. A kill switch stops ingestion and
   downstream publication; it does not and cannot cancel orders because Proto has no route to
   place one.

The reusable policy in `services/security/live_data_policy.py` enforces endpoint, method, and
credential invariants independently of any provider adapter.

## Options Considered

### Option A: Public data adapters inside Proto (selected)

| Dimension | Assessment |
|---|---|
| Complexity | Medium |
| Security | Strong when policy is enforced before network access |
| Latency | Direct provider connection |
| Operations | Requires feed health, reconnect, and rate-limit controls |

**Pros:** Minimal infrastructure, transparent contracts, provider diversity.

**Cons:** Proto owns normalization and connection resilience.

### Option B: Authenticated provider/account integration

| Dimension | Assessment |
|---|---|
| Complexity | High |
| Security | Unacceptable for this boundary |
| Latency | Direct provider connection |
| Operations | Adds secret rotation and account risk |

**Pros:** Could expose private account state.

**Cons:** Creates paths toward trading and custody; explicitly rejected.

### Option C: External read-only data gateway

| Dimension | Assessment |
|---|---|
| Complexity | High |
| Security | Strong isolation |
| Latency | Additional hop |
| Operations | Separate service and deployment |

**Pros:** Strong network separation and centralized provider management.

**Cons:** Premature operational cost for the current scale; remains a future option.

## Trade-off Analysis

Direct public adapters provide the smallest useful live-data surface. Exact host allowlisting
and public-only protocols reduce SSRF and credential leakage risks, at the cost of explicitly
onboarding each provider. Rejecting all private trading credentials means providers that require
account authentication cannot be used, even for nominally read-only endpoints; this is an
intentional safety trade-off.

## Consequences

- Live observations can drive dashboards and research analytics without enabling execution.
- Provider onboarding requires review of public endpoints, schemas, rate limits, and terms.
- Feed disconnects and gaps become visible operational states rather than silent degradation.
- Simulation and live observations remain separate provenance domains.
- A future order-routing capability would require a new ADR, separate service boundary, and
  explicit security model; it cannot be added as an extension of this mode.

## Action Items

1. [x] Add reusable endpoint, method, and credential policy with unit tests.
2. [x] Add the runtime enum and enforce mode-specific API route availability.
3. [x] Implement one public feed adapter with bounded reconnect/backoff and no authentication.
4. [x] Publish feed freshness, reconnect, dropped-frame, and sequence-gap metrics.
5. [x] Add integration and attack tests proving execution endpoints remain unreachable in live
   mode.
