"""create promotion scopes

Revision ID: 0010_promotion_scopes
Revises: 0009_oms_state
Create Date: 2026-10-14
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0010_promotion_scopes"
down_revision: str | None = "0009_oms_state"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLE_NAME = "promotion_scopes"


def upgrade() -> None:
    op.create_table(
        TABLE_NAME,
        sa.Column("scope_key", sa.String(length=400), nullable=False),
        sa.Column("stage", sa.String(length=16), nullable=False),
        sa.Column("record", sa.Text(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("scope_key"),
    )


def downgrade() -> None:
    op.drop_table(TABLE_NAME)
