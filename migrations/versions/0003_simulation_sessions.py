"""add durable simulation session boundaries

Revision ID: 0003_simulation_sessions
Revises: 0002_canonical_persistence
Create Date: 2026-08-31
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0003_simulation_sessions"
down_revision: str | None = "0002_canonical_persistence"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "simulation_sessions",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_simulation_sessions_active",
        "simulation_sessions",
        ["active"],
    )
    op.add_column(
        "simulation_fills",
        sa.Column(
            "session_id",
            sa.String(length=36),
            nullable=False,
            server_default="legacy",
        ),
    )
    op.create_index(
        "ix_simulation_fills_session_id",
        "simulation_fills",
        ["session_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_simulation_fills_session_id", table_name="simulation_fills")
    op.drop_column("simulation_fills", "session_id")
    op.drop_index("ix_simulation_sessions_active", table_name="simulation_sessions")
    op.drop_table("simulation_sessions")
