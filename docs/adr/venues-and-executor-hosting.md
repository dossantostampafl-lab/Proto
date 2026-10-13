# ADR: Venues and executor hosting for the first certified paths

- Status: Accepted (Phase 0 of `docs/plans/2026-10-13-tradingview-agent-trading.md`)
- Date: 2026-10-13
- Decision owner: Repository owner

## Context

The trading-automation plan targets crypto, B3 and forex up to a minimal-capital canary.
Each venue is its own certified path (`RESEARCH → … → CANARY → LIVE`, scoped per account,
venue, strategy, model version and credential set). The owner chose the venues and the
hosting provider below. Forex has no venue yet and stays out of scope until one is chosen.

## Decision

### Venues, in certification order

| Order | Venue id | Market | Integration | Notes |
|---:|---|---|---|---|
| 0 | `PAPER` | All | `PaperVenueAdapter` (in-process) | Deterministic fills against a quote; never real money |
| 1 | `BINANCE_SPOT` | Crypto spot (BRL/USDT pairs) | Binance Spot REST + user-data WebSocket | Public feeds already exist in `services/market_data` |
| 2 | `XP_B3` | B3 equities/futures | MetaTrader 5 terminal through XP | MT5's Python API runs only on Windows; needs its own host |

Spot only: no margin, futures or lending products on Binance, which keeps gross leverage at
or below 1.0x by construction. B3 derivatives (mini-index, mini-dollar) need a separate
leverage check before they can leave PAPER.

### Executor hosting: Oracle Cloud (OCI)

```text
 internet ── TradingView webhooks ──> public API (existing Oracle instance, 443 only)
                                          │  intents + permits (private network only)
                                          v
                               executor (separate instance, private subnet)
                                 ├── Binance adapter ── NAT gateway (fixed egress IP) ──> Binance
                                 └── XP adapter ──> Windows VM running MT5 (private subnet) ──> XP/B3
```

- The executor runs on its own instance in a private subnet with no public IP and no inbound
  internet access. It accepts work only from the OMS over the private network.
- It is the only process that holds venue credentials. It reads them at startup from OCI
  Vault, through an instance principal scoped to its own secrets. Credentials never reach the
  public API, agents, prompts, frontend, logs or CI.
- Binance API keys: spot trading enabled; withdrawals, transfers, margin and futures
  disabled; restricted to the NAT gateway's reserved egress IP.
- XP/MT5: a dedicated Windows VM in the same private subnet runs the MT5 terminal logged into
  the trading account. The executor drives it through a narrow RPC that only exposes
  submit, cancel, query, positions and balances. The account's password and electronic
  signature stay on that VM.
- Each environment (paper, canary, live) and each venue uses separate credentials and
  separate Vault secrets.

## Consequences

- Phase 0 ships the venue-neutral contracts (`services/execution`) and the paper venue only.
  `financial_connectivity=false` and `real_money_execution=false` remain true everywhere.
- Binance and XP adapters arrive in Phase 5, behind the Risk Engine, OMS and ledger from
  Phases 3 and 4. Each adapter must pass the same contract tests as `PaperVenueAdapter`.
- The XP path costs one extra Windows VM. It depends on XP continuing to offer MT5 for B3
  and allowing automated orders on the account. Confirm both with XP before Phase 5.
- Forex needs a venue that accepts Brazilian residents and supports a 1.0x leverage cap.
  It gets its own ADR when chosen.
