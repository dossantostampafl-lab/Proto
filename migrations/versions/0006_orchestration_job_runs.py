"""create durable proto brain job runtime

Revision ID: 0006_orchestration_job_runs
Revises: 0005_research_experiments
Create Date: 2026-09-02
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0006_orchestration_job_runs"
down_revision: str | None = "0005_research_experiments"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLE_NAME = "orchestration_job_runs"


def upgrade() -> None:
    op.create_table(
        TABLE_NAME,
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("idempotency_key", sa.String(length=160), nullable=False),
        sa.Column("job_name", sa.String(length=120), nullable=False),
        sa.Column("capability", sa.String(length=40), nullable=False),
        sa.Column("mode", sa.String(length=32), nullable=False),
        sa.Column("payload_json", sa.Text(), nullable=False),
        sa.Column("state", sa.String(length=32), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("max_attempts", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("not_before", sa.DateTime(timezone=True), nullable=False),
        sa.Column("lease_owner", sa.String(length=120), nullable=True),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("heartbeat_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("result_json", sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "idempotency_key",
            name="uq_orchestration_job_runs_idempotency_key",
        ),
    )
    for column in (
        "idempotency_key",
        "job_name",
        "capability",
        "mode",
        "state",
        "created_at",
        "updated_at",
        "not_before",
        "lease_owner",
        "lease_expires_at",
    ):
        op.create_index(f"ix_{TABLE_NAME}_{column}", TABLE_NAME, [column])


def downgrade() -> None:
    for column in reversed(
        (
            "idempotency_key",
            "job_name",
            "capability",
            "mode",
            "state",
            "created_at",
            "updated_at",
            "not_before",
            "lease_owner",
            "lease_expires_at",
        )
    ):
        op.drop_index(f"ix_{TABLE_NAME}_{column}", table_name=TABLE_NAME)
    op.drop_table(TABLE_NAME)
