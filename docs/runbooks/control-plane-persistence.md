# Control-plane persistence: what survives a deploy

Production runs `SYSTEM_MODE=LIVE_MONITORING`, where `PERSISTENCE_ENABLED` (simulation
persistence) is refused by a settings validator. Before migration `0013_control_state` that also
left the control plane in memory, so every deploy reset the daily-loss and drawdown baselines,
cleared lockdowns and kill switches, forgot spent permits and restarted promotion coverage.

The control plane now has its own durable store, separate from simulation persistence.

## Switch

| Setting | Effect |
|---|---|
| `CONTROL_PERSISTENCE_ENABLED=true` | On in the image (`Dockerfile`). Uses `DATABASE_URL`. |
| derived | Also on in production with a Postgres `DATABASE_URL`, or with `LIVE_PERSISTENCE_ENABLED` and Postgres (same rules as orchestration persistence). |
| `PERSISTENCE_ENABLED` | Unchanged. Still refused in production LIVE_MONITORING. |

The control store shares the engine already open on `DATABASE_URL` (orchestration's when it
exists). It creates only control-plane tables (`init_control_database`), never the simulation
tables (`simulation_sessions`, `simulation_fills`, canonical, orchestration).

## What is durable now

| State | Table | On restart |
|---|---|---|
| Operational journal | `operational_journal` | Head restored and the chain re-verified from GENESIS |
| Kill switches (global, venue, strategy) and the lockdown latch | `control_state` (`risk_controls`) | Restored before any proposal is assessed. A restore only adds; it never releases |
| Start-of-day and peak equity per account and quote currency | `oms_equity_checkpoints` | The 1% daily-loss and 5% drawdown bases continue (exact decimals as text) |
| Spent permits | `oms_consumed_permits` | A permit stays single use (primary-key insert before the order is sent) |
| Paper OMS orders and ledger | `oms_orders`, `ledger_entries` | Open orders come back as UNKNOWN for the operator |
| Promotion scopes (stage, clean-since, evidence, violations) | `promotion_scopes` | The 7-day clean paper period keeps accumulating |
| Watchdog coverage window | `control_state` (`watchdog_coverage`) | A deploy shorter than the 30 s gap keeps the 24 h window; a longer one breaks it on the next beat |
| Agent roster | `agent_roster`, `agent_orders` | Agents, books and eliminations continue |

The executor keeps its own durable state, as before.

## Fail closed

When the store is configured but unreachable (at startup or later), `control_state` reports
`UNAVAILABLE` and:

- the Risk Engine refuses every proposal with `control_state_available`. It does not fall back to memory;
- the journal refuses writes (`UNVERIFIED`) instead of forking the chain from an empty head;
- operator releases of kill switches and lockdowns are refused with 503 and the controls are restored. Triggers still apply in memory, since stricter is always allowed;
- `/ready` returns 503.

The store retries every 15 s. Once the database answers, the saved controls are re-read and
merged (stricter wins), and new risk is accepted again.

## Labels

- `GET /ready` → `control_plane` with `status` (`DURABLE`, `MEMORY`, `PENDING`, `UNAVAILABLE`),
  `durable`, `scope`, `accepts_new_risk` and the store list.
- `GET /events/journal/verify` → `durable` and `control_plane`. `scope` is `durable_history` or
  `memory_window`.
- `GET /v1/risk/status` → `controls_persistence`; `GET /v1/watchdog/status` → `coverage_durable`;
  `GET /v1/floor/state` → `control_plane`.

`durable` is false in memory mode and with an in-memory SQLite URL. Only `DURABLE` on a
persistent database is labelled durable.

## Migration

`alembic upgrade head` applies `0013_control_state` (down revision `0012_agent_roster`). Startup
also runs `create_all` for the control-plane tables, so a missed migration does not fail open.
