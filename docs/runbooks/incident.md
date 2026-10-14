# Incident runbook (Phase 7)

For the operator on call. Every command assumes:

```bash
export API=https://<public-origin>              # the public API
export OP="X-Proto-Operator-Token: $OPERATOR_API_TOKEN"
```

**The first move is always safe:** halting never loses data and never moves money. When in
doubt, halt first and investigate after.

```bash
curl -fsS -X POST "$API/v1/risk/killswitch" -H "$OP" -H 'Content-Type: application/json' \
  -d '{"scope":"GLOBAL","action":"TRIGGER"}'
```

## 1. See what is going on

```bash
curl -fsS "$API/v1/watchdog/status"     # is the watchdog beating? its last checks and trips
curl -fsS "$API/v1/risk/status"         # kill switches, lockdown, permit signing mode
curl -fsS "$API/v1/floor/state"         # journal integrity, accounts, recent agent actions
curl -fsS "$API/v1/oms/ledger"          # balances (from the executor in executor mode)
curl -fsS -X POST "$API/v1/oms/reconcile" -H "$OP"   # ledger vs venue, now
```

The trading floor shows the same: the Risk Engine desk turns red on a watchdog halt or a lockdown,
and the Reconciliation desk on a divergence.

## 2. Alert or trip reason → cause → action

| Alert / watchdog reason | What it means | Action |
|---|---|---|
| `ProtoWatchdogTripped` | The watchdog halted trading | Read `last_report` and `recent_trips` in `/v1/watchdog/status`, then the row for its reason below |
| `daily_loss:<asset>` / `drawdown:<asset>` · `ProtoLockdownLatched` | A constitutional limit was breached | Do **not** release. Review the losing positions; reduce-only exits still pass. The lockdown can only be released once the account no longer breaches the limit (next trading day for the daily loss) |
| `reconciliation_divergence` | The ledger and the venue disagree | The executor halted itself and the scope was demoted. Compare `/v1/oms/ledger` with the venue; resolve `UNKNOWN` orders (`POST /v1/oms/orders/<id>/resolve`). Release only once `/v1/oms/reconcile` is consistent |
| `reconciliation_stale` · `executor_unreachable` | The executor stopped reconciling or answering | Check the executor instance (`systemctl status`, logs). New risk is already refused: an unreachable executor counts as infinite exposure |
| `api_unreachable` | The watchdog cannot read the API | The watchdog halted the executor. Bring the API back; the watchdog keeps the executor halted until an operator releases it |
| `journal_<state>` · `ProtoJournalInvalid` | Audit lineage is broken or unavailable | Do not trade without audit. Check the database behind the journal; keep the halt until the journal verifies |
| `account_unavailable:<asset>` | An account cannot be read | Check the ledger database and the executor link |
| `market_data_stale` | No fresh public market data while execution is on | The Risk Engine already refuses stale quotes. Check the feed (`/live/source-health`) |
| `ProtoWatchdogDown` | No watchdog heartbeat for a minute | Nothing halts trading from outside the API. Halt manually (above) unless the watchdog is back within minutes; restart it (`systemctl restart proto-watchdog`) |
| `ProtoRealMoneyExecutionEnabled` | Real-money execution reported as enabled | Halt at once. No live scope is certified; treat it as a security incident |

## 3. Releasing, only after the cause is fixed

Release in this order, and only once the condition that tripped is gone. The watchdog halts again
on its next cycle if it is not.

```bash
# 1. The executor (relayed through the API; refused while still diverged).
curl -fsS -X POST "$API/v1/oms/executor/halt/release" -H "$OP"

# 2. A latched lockdown (refused while the account still breaches a limit).
curl -fsS -X POST "$API/v1/risk/lockdown/release" -H "$OP"

# 3. The global kill switch, last.
curl -fsS -X POST "$API/v1/risk/killswitch" -H "$OP" -H 'Content-Type: application/json' \
  -d '{"scope":"GLOBAL","action":"RELEASE"}'
```

Venue and strategy kill switches take `{"scope":"VENUE","key":"BINANCE_SPOT", ...}` or
`{"scope":"STRATEGY","key":"<strategy_id>", ...}`. The legacy runtime kill switch is
`POST /killswitch/reset`.

## 4. After the incident

- **Scope:** a divergence or a lockdown already demoted every CANARY scope on the venue and reset
  its clean week (`PROMOTION_VIOLATION` in the journal). Re-promoting needs a new clean week and
  fresh evidence.
- **Record:** write what happened, the trip reasons, the timeline from the journal
  (`GET /events/journal`, verified with `GET /events/journal/verify`), and what changed.
- **Rollback:** if the incident came from a change, follow `docs/runbooks/rollback.md`.

## Never

- release a halt to "see if it happens again";
- raise a limit, disable the watchdog or remove a kill switch to get trading back;
- move funds, withdraw or transfer: the system cannot, and no runbook step asks for it.
