from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import JSON, Column, DateTime, MetaData, String, Table

canonical_metadata = MetaData()

BASE_CANONICAL_TABLE_NAMES = (
    "markets",
    "market_ticks",
    "orderbook_snapshots",
    "trades",
    "prediction_contracts",
    "model_predictions",
    "fair_values",
    "edges",
    "signals",
    "risk_decisions",
    "orders",
    "fills",
    "positions",
    "hedges",
    "portfolio_snapshots",
    "pnl_snapshots",
    "model_metrics",
    "calibration_metrics",
    "hawkes_states",
    "replay_sessions",
    "system_events",
    "audit_events",
)

CANONICAL_TABLE_NAMES = (*BASE_CANONICAL_TABLE_NAMES, "research_experiments")


def _timestamp() -> datetime:
    return datetime.now(UTC)


def _canonical_table(name: str) -> Table:
    return Table(
        name,
        canonical_metadata,
        Column("id", String(64), primary_key=True),
        Column("created_at", DateTime(timezone=True), nullable=False, default=_timestamp),
        Column("correlation_id", String(64), nullable=True, index=True),
        Column("payload", JSON, nullable=False, default=dict),
    )


CANONICAL_TABLES = {name: _canonical_table(name) for name in CANONICAL_TABLE_NAMES}
