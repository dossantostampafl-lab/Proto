"""create standalone live market tick journal

Revision ID: 0001_live_market_ticks
Revises:
Create Date: 2026-08-29
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0001_live_market_ticks"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "live_market_ticks",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("venue", sa.String(length=64), nullable=False),
        sa.Column("symbol", sa.String(length=16), nullable=False),
        sa.Column("connection_generation", sa.Integer(), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("source_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("persisted_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("bid", sa.Float(), nullable=False),
        sa.Column("ask", sa.Float(), nullable=False),
        sa.Column("last", sa.Float(), nullable=False),
        sa.Column("volume", sa.Float(), nullable=False),
        sa.Column("bid_size", sa.Float(), nullable=False),
        sa.Column("ask_size", sa.Float(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "venue",
            "symbol",
            "sequence",
            name="uq_live_market_ticks_source_sequence",
        ),
    )
    op.create_index("ix_live_market_ticks_venue", "live_market_ticks", ["venue"])
    op.create_index("ix_live_market_ticks_symbol", "live_market_ticks", ["symbol"])
    op.create_index(
        "ix_live_market_ticks_connection_generation",
        "live_market_ticks",
        ["connection_generation"],
    )
    op.create_index("ix_live_market_ticks_source_at", "live_market_ticks", ["source_at"])
    op.create_index("ix_live_market_ticks_received_at", "live_market_ticks", ["received_at"])
    op.create_index("ix_live_market_ticks_persisted_at", "live_market_ticks", ["persisted_at"])
    op.create_index(
        "ix_live_market_ticks_symbol_received",
        "live_market_ticks",
        ["symbol", "received_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_live_market_ticks_symbol_received", table_name="live_market_ticks")
    op.drop_index("ix_live_market_ticks_persisted_at", table_name="live_market_ticks")
    op.drop_index("ix_live_market_ticks_received_at", table_name="live_market_ticks")
    op.drop_index("ix_live_market_ticks_source_at", table_name="live_market_ticks")
    op.drop_index("ix_live_market_ticks_connection_generation", table_name="live_market_ticks")
    op.drop_index("ix_live_market_ticks_symbol", table_name="live_market_ticks")
    op.drop_index("ix_live_market_ticks_venue", table_name="live_market_ticks")
    op.drop_table("live_market_ticks")
