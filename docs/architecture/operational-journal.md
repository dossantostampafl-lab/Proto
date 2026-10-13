# Operational journal

The operational journal is PROTO's tamper-evident audit lineage. It records operator commands,
accepted TradingView signals, and later the intents, permits, orders and fills of the execution
chain.

## Guarantees

| Guarantee | How |
|---|---|
| Tamper evidence | Each event hashes its content and its predecessor's hash. Editing, deleting, reordering or renumbering any event breaks verification from `GENESIS` |
| No gaps | `sequence` is the primary key and must be exactly the previous one plus one |
| Durable before visible | With persistence enabled, an event is committed to `operational_journal` before the in-memory head advances. A failed write raises `JournalUnavailable` and records nothing |
| Survives restarts | At startup the whole history is streamed back in sequence order and verified. The chain continues from the persisted head |
| Fails closed | A broken chain, a write before the history is loaded, or a second concurrent writer puts the journal in `CORRUPTED` or `UNVERIFIED` state, and new writes are refused |
| Idempotent | A repeated `idempotency_key` returns the original event with `created=false` instead of raising. The database's unique constraint is the authority across restarts |
| Stable hashing | Payloads must be finite, JSON-native values. They are stored as canonical JSON, so what is hashed is exactly what is kept |
| Bounded memory | Only the head, the last 10,000 events and an LRU cache of 100,000 idempotency keys stay in memory |

## Components

- `services/events/journal.py`: the pure chain. It holds the head, prepares and commits events
  and verifies links.
- `services/events/journal_store.py`: the `operational_journal` table. Sequence, event id,
  idempotency key, previous hash and hash are all unique. Created by migration
  `0007_operational_journal` and by `init_database`.
- `apps/api/app/event_state.py`: `OperationalJournal`, the single writer used by the API.

## Operations

- `GET /events/journal?limit=N` returns the head, the integrity state and the latest events.
- `GET /events/journal/verify` re-verifies the entire durable history from `GENESIS` against the
  in-memory head. It returns `409` when the chain is invalid.
- When a state-changing command succeeds but its audit event cannot be written, the response
  carries `X-Proto-Audit: unrecorded` and the `operational_journal_write_failures` metric
  increments.
- `CORRUPTED` is never cleared automatically. Investigate the reported sequence, restore the
  table from backup, and restart.
- Exactly one API process may write the journal. Run additional replicas read-only, or a second
  writer is detected and the journal locks itself.

Research lineage (`quant-lineage`) keeps using the generic `audit_events` table. The operational
journal no longer writes there.
