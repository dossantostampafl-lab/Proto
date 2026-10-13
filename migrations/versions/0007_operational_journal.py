"""create durable hash-chained operational journal

Revision ID: 0007_operational_journal
Revises: 0006_orchestration_job_runs
Create Date: 2026-10-13
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0007_operational_journal"
down_revision: str | None = "0006_orchestration_job_runs"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLE_NAME = "operational_journal"


def upgrade() -> None:
    op.create_table(
        TABLE_NAME,
        sa.Column("sequence", sa.Integer(), autoincrement=False, nullable=False),
        sa.Column("event_id", sa.String(length=36), nullable=False),
        sa.Column("timestamp", sa.String(length=40), nullable=False),
        sa.Column("event_type", sa.String(length=120), nullable=False),
        sa.Column("source", sa.String(length=120), nullable=False),
        sa.Column("correlation_id", sa.String(length=36), nullable=False),
        sa.Column("idempotency_key", sa.String(length=200), nullable=True),
        sa.Column("payload_json", sa.Text(), nullable=False),
        sa.Column("previous_hash", sa.String(length=64), nullable=False),
        sa.Column("hash", sa.String(length=64), nullable=False),
        sa.PrimaryKeyConstraint("sequence"),
        sa.UniqueConstraint("event_id"),
        sa.UniqueConstraint("idempotency_key"),
        sa.UniqueConstraint("previous_hash"),
        sa.UniqueConstraint("hash"),
    )
    op.create_index("ix_operational_journal_event_type", TABLE_NAME, ["event_type"])
    op.create_index("ix_operational_journal_correlation_id", TABLE_NAME, ["correlation_id"])


def downgrade() -> None:
    op.drop_index("ix_operational_journal_correlation_id", table_name=TABLE_NAME)
    op.drop_index("ix_operational_journal_event_type", table_name=TABLE_NAME)
    op.drop_table(TABLE_NAME)
