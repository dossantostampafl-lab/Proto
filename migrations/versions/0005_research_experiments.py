"""create reproducible research experiment registry

Revision ID: 0005_research_experiments
Revises: 0004_session_scoped_fill_idempotency
Create Date: 2026-08-31
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0005_research_experiments"
down_revision: str | None = "0004_session_scoped_fill_idempotency"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLE_NAME = "research_experiments"


def upgrade() -> None:
    op.create_table(
        TABLE_NAME,
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("correlation_id", sa.String(length=64), nullable=True),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        f"ix_{TABLE_NAME}_created_at",
        TABLE_NAME,
        ["created_at"],
    )
    op.create_index(
        f"ix_{TABLE_NAME}_correlation_id",
        TABLE_NAME,
        ["correlation_id"],
    )


def downgrade() -> None:
    op.drop_index(f"ix_{TABLE_NAME}_correlation_id", table_name=TABLE_NAME)
    op.drop_index(f"ix_{TABLE_NAME}_created_at", table_name=TABLE_NAME)
    op.drop_table(TABLE_NAME)
