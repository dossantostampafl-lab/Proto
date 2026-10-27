# Rollback runbook (Phase 7)

How to step back from a change, from the smallest step to the largest. Halt first if anything is
trading (`docs/runbooks/incident.md`). Commands use `$API` and `$OP` as defined there.

## 1. A promotion scope

Demote one stage at a time; demotion is never refused:

```bash
curl -fsS -X POST "$API/v1/promotion/demote" -H "$OP" -H 'Content-Type: application/json' \
  -d '{"scope":{"account":"...","venue":"BINANCE_SPOT","strategy_id":"...","model_version":"...","credential_id":"..."},"reason":"rollback: <why>"}'
curl -fsS "$API/v1/promotion/scopes"   # confirm the stage
```

A CANARY intent for a scope below CANARY is refused by the Risk Engine from the next intent on.
The `ROLLBACK_DRILL` (`docs/drills/drills.md`) proves this path end to end.

## 2. Configuration (no deploy)

Each step makes the system strictly more conservative. Restart the API after changing its
environment.

| Step | Variable | Effect |
|---|---|---|
| Stop the AI reviewer | `AGENT_REVIEWER=deterministic` | Back to the deterministic reviewer |
| Stop proposals | `AGENT_PIPELINE_ENABLED=false` | Agents produce nothing; nothing reaches the Risk Engine |
| Stop paper execution | `OMS_PAPER_EXECUTION_ENABLED=false` | Permits expire unused |
| Leave the executor | `OMS_EXECUTION_BACKEND=paper` | The API stops dispatching to the testnet executor |

## 3. Code

- **Release:** roll the API (and, separately, the executor) back to the previous image tag or
  commit, and keep the watchdog running throughout; it does not depend on either's code.
- **Migrations:** all but `0003`, `0004` and `0011` only add tables (those three change existing
  columns or constraints), so older code runs on a newer schema. Do not downgrade a production database; prefer a
  forward fix. Check `alembic history` first. The Oracle deployment never runs Alembic: the API and
  the executor create missing tables at startup (`create_all`), so their databases carry no
  `alembic_version` and `alembic upgrade head` there fails on the first existing table.
- **Verify** after rolling back: `GET /health`, `GET /v1/watchdog/status` (active),
  `GET /v1/floor/state` (journal `VALID`), and `POST /v1/oms/reconcile` (consistent).

## 4. The executor

```bash
# Stop new orders without stopping reconciliation: halt it from the API side.
curl -fsS -X POST "$API/v1/risk/killswitch" -H "$OP" -H 'Content-Type: application/json' \
  -d '{"scope":"VENUE","key":"BINANCE_SPOT","action":"TRIGGER"}'
```

Stopping the executor process is safe: on restart it reloads the order book, marks open orders
`UNKNOWN`, resolves them by querying the venue and reconciles before accepting orders
(`RECOVERY_DRILL`). Keep its database; never delete it to "start clean".
