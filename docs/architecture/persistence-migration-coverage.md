# Persistence migration coverage

Alembic now tracks all durable metadata used by PROTO: the standalone live tick journal, paper/simulation fills, and the canonical research/analytics registry.

The migration chain is responsible for reproducible production schema creation. Runtime `create_all` remains a development/bootstrap convenience and is not the source of truth for deployments.

Revision `0002_canonical_persistence` creates the canonical tables required for research, quant analytics, risk decisions, paper execution, portfolio/P&L snapshots, replay state, system events, and audit events, plus the simulation fill journal. Correlation and creation-time indexes are created for each canonical event table.

The live read-only boundary remains separate: `live_market_ticks` is owned by revision `0001_live_market_ticks` and no account, brokerage, exchange credential, order-routing, custody, deposit, withdrawal, leverage, or real-money execution tables are introduced.
