"""Persist single-use authorizations before OMS submission.

Revision ID: 0008_execution_consumed_permits
Revises: 0007_operational_journal
Create Date: 2026-10-13
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0008_execution_consumed_permits"
down_revision: str | None = "0007_operational_journal"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "execution_consumed_permits",
        sa.Column("permit_id", sa.String(length=36), nullable=False),
        sa.Column("intent_id", sa.String(length=36), nullable=False),
        sa.Column("intent_fingerprint", sa.String(length=64), nullable=False),
        sa.Column("client_order_id", sa.String(length=36), nullable=False),
        sa.Column("consumed_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("permit_id"),
        sa.UniqueConstraint(
            "client_order_id", name="uq_execution_permit_client_order"
        ),
    )


def downgrade() -> None:
    op.drop_table("execution_consumed_permits")
