"""create the durable control-plane state (kill switches, lockdown, watchdog coverage)

Revision ID: 0013_control_state
Revises: 0012_agent_roster
Create Date: 2026-10-17
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0013_control_state"
down_revision: str | None = "0012_agent_roster"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLE_NAME = "control_state"


def upgrade() -> None:
    op.create_table(
        TABLE_NAME,
        sa.Column("state_key", sa.String(length=80), nullable=False),
        sa.Column("document", sa.Text(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("state_key"),
    )


def downgrade() -> None:
    op.drop_table(TABLE_NAME)
