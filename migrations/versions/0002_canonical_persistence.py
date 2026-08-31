"""create canonical research and paper persistence schemas

Revision ID: 0002_canonical_persistence
Revises: 0001_live_market_ticks
Create Date: 2026-08-30
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0002_canonical_persistence"
down_revision: str | None = "0001_live_market_ticks"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

CANONICAL_TABLE_NAMES = (
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


def upgrade() -> None:
    op.create_table(
        "simulation_fills",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("order_id", sa.String(length=36), nullable=False),
        sa.Column("market_id", sa.String(length=120), nullable=False),
        sa.Column("asset", sa.String(length=16), nullable=False),
        sa.Column("side", sa.String(length=8), nullable=False),
        sa.Column("filled_quantity", sa.Float(), nullable=False),
        sa.Column("fill_price", sa.Float(), nullable=False),
        sa.Column("fee", sa.Float(), nullable=False),
        sa.Column("slippage_bps", sa.Float(), nullable=False),
        sa.Column("filled_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("order_id", name="uq_simulation_fills_order_id"),
    )
    op.create_index("ix_simulation_fills_order_id", "simulation_fills", ["order_id"])
    op.create_index("ix_simulation_fills_market_id", "simulation_fills", ["market_id"])
    op.create_index("ix_simulation_fills_asset", "simulation_fills", ["asset"])
    op.create_index("ix_simulation_fills_filled_at", "simulation_fills", ["filled_at"])

    for table_name in CANONICAL_TABLE_NAMES:
        op.create_table(
            table_name,
            sa.Column("id", sa.String(length=64), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("correlation_id", sa.String(length=64), nullable=True),
            sa.Column("payload", sa.JSON(), nullable=False),
            sa.PrimaryKeyConstraint("id"),
        )
        op.create_index(
            f"ix_{table_name}_created_at",
            table_name,
            ["created_at"],
        )
        op.create_index(
            f"ix_{table_name}_correlation_id",
            table_name,
            ["correlation_id"],
        )


def downgrade() -> None:
    for table_name in reversed(CANONICAL_TABLE_NAMES):
        op.drop_index(f"ix_{table_name}_correlation_id", table_name=table_name)
        op.drop_index(f"ix_{table_name}_created_at", table_name=table_name)
        op.drop_table(table_name)

    op.drop_index("ix_simulation_fills_filled_at", table_name="simulation_fills")
    op.drop_index("ix_simulation_fills_asset", table_name="simulation_fills")
    op.drop_index("ix_simulation_fills_market_id", table_name="simulation_fills")
    op.drop_index("ix_simulation_fills_order_id", table_name="simulation_fills")
    op.drop_table("simulation_fills")
