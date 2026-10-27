"""create the decision memory table that init_database already created at runtime

Revision ID: 0015_decision_memory
Revises: 0014_agent_conversations
Create Date: 2026-10-27

`decision_memory` (services/orchestration/memory.py) has been created by `init_database` and
`DecisionMemoryStore.init_schema` without a migration, so a database managed only by Alembic
lacked it. A database where the runtime already created it is left as it is.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0015_decision_memory"
down_revision: str | None = "0014_agent_conversations"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLE_NAME = "decision_memory"
INDEXED_COLUMNS = ("mission_id", "instrument_id", "observed_at", "recorded_at", "stage")


def upgrade() -> None:
    if sa.inspect(op.get_bind()).has_table(TABLE_NAME):
        return
    op.create_table(
        TABLE_NAME,
        sa.Column("decision_id", sa.String(length=36), nullable=False),
        sa.Column("mission_id", sa.String(length=36), nullable=True),
        sa.Column("instrument_id", sa.String(length=160), nullable=False),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("recorded_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("stage", sa.String(length=40), nullable=False),
        sa.Column("entry_json", sa.Text(), nullable=False),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("outcome_json", sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint("decision_id"),
    )
    for column in INDEXED_COLUMNS:
        op.create_index(f"ix_{TABLE_NAME}_{column}", TABLE_NAME, [column])


def downgrade() -> None:
    # No-op on purpose: upgrade() only creates decision_memory when the runtime had not already
    # created it, so dropping it here could delete recorded decisions this revision never owned.
    return None
