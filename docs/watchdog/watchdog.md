# Independent watchdog (Phase 6b)

The watchdog is a second line of defence, separate from the Risk Engine. It runs as its own
process, reads the public API and the executor from outside over HTTP, applies the constitutional
limits itself, and **halts trading** when a check fails. It can only halt: releasing a halt stays
an operator action.

```bash
python -m apps.watchdog   # needs WATCHDOG_API_URL and WATCHDOG_API_TOKEN
```

Run it on its own instance, or at least its own process and service account, so an outage of the
API or the executor does not take it down too. `apps/watchdog` and `services/watchdog` import
neither the API, nor the executor, nor the OMS, risk or venue code; a test enforces it.

## What it checks, every cycle (default 5 s)

| Check | Trips when | Reason |
|---|---|---|
| API reachable | `/health` or `/v1/floor/state` fail for 3 cycles in a row | `api_unreachable` |
| Journal | the operational journal is not `VALID` | `journal_<state>` |
| Accounts | an account cannot be read for 3 cycles | `account_unavailable:<asset>` |
| Daily loss | equity / start-of-day − 1 ≤ −1% (constitution) | `daily_loss:<asset>` |
| Drawdown | equity / peak − 1 ≤ −5% (constitution) | `drawdown:<asset>` |
| Market data | `/live/ready` is not ready for 6 cycles **while execution is on** | `market_data_stale` |
| Executor reachable | its status fails for 3 cycles (when configured) | `executor_unreachable` |
| Reconciliation | the executor reports a divergence | `reconciliation_divergence` |
| Reconciliation age | its last reconciliation is older than 3 minutes | `reconciliation_stale` |

`WATCHDOG_DAILY_LOSS_FRACTION` and `WATCHDOG_DRAWDOWN_FRACTION` can make the loss checks stricter
(a fraction of the limit, at most 1). A looser value is refused.

## What it does when a check trips

- **API:** `POST /v1/watchdog/halt` triggers the global kill switch and journals
  `WATCHDOG_TRIPPED` (the trading floor shows it on the Risk Engine desk).
- **Executor:** `POST /v1/watchdog/halt` stops it accepting orders; it journals `WATCHDOG_HALT`.
- **Both, every cycle the condition persists**, until each side confirms. If an operator releases
  the kill switch while the condition still holds, the next cycle halts again. The API's kill
  switch lives in memory, so after an API restart the watchdog re-halts it the same way.
- When every check is clear again the incident ends. The halts stay until an operator releases
  them (`POST /v1/risk/killswitch` with `RELEASE`, `POST /v1/halt/release` on the executor).

## Credentials: halt-only tokens

| Side | Variable | Can | Cannot |
|---|---|---|---|
| API | `WATCHDOG_API_TOKEN` (header `X-Proto-Watchdog-Token`) | heartbeat, trigger the global kill switch | release anything, reach operator routes |
| Executor | `EXECUTOR_WATCHDOG_TOKEN` (bearer) | read its status, halt it | place, resolve or cancel orders, release a halt |

Each token must differ from the operator and executor tokens; both services refuse to start
otherwise. Without a configured token the watchdog routes answer 503.

## Heartbeats and the `WATCHDOG_ACTIVE` evidence

Each cycle ends with `POST /v1/watchdog/heartbeat`. The API measures how long the watchdog has
watched without a gap (a heartbeat more than 30 s after the previous one, or from another
watchdog id, restarts the window). `GET /v1/watchdog/status` shows it.

`POST /v1/promotion/evidence/watchdog` (operator, `{scope}`) records `WATCHDOG_ACTIVE` from that
measurement, never from a claim: it **passes only after 24 h of continuous coverage** with the
watchdog still beating. Otherwise it records a failed evidence with the measured hours.

## Configuration

| Variable | Default | |
|---|---|---|
| `WATCHDOG_ID` | `watchdog-1` | Name in heartbeats and the journal |
| `WATCHDOG_API_URL` | | Public API base URL |
| `WATCHDOG_API_TOKEN` | | Same value as the API's `WATCHDOG_API_TOKEN` |
| `WATCHDOG_EXECUTOR_URL` · `WATCHDOG_EXECUTOR_TOKEN` | | Optional, together. The token is the executor's `EXECUTOR_WATCHDOG_TOKEN` |
| `WATCHDOG_INTERVAL_SECONDS` | `5` | 1 to 60 |
| `WATCHDOG_DAILY_LOSS_FRACTION` · `WATCHDOG_DRAWDOWN_FRACTION` | `1` | Tighter-only fractions of the limits |
