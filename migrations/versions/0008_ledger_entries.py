"""create transactional ledger entries

Revision ID: 0008_ledger_entries
Revises: 0007_operational_journal
Create Date: 2026-10-13
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0008_ledger_entries"
down_revision: str | None = "0007_operational_journal"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLE_NAME = "ledger_entries"


def upgrade() -> None:
    op.create_table(
        TABLE_NAME,
        sa.Column("entry_id", sa.String(length=36), nullable=False),
        sa.Column("account_id", sa.String(length=80), nullable=False),
        sa.Column("venue", sa.String(length=32), nullable=False),
        sa.Column("asset", sa.String(length=20), nullable=False),
        sa.Column("amount", sa.Numeric(38, 18), nullable=False),
        sa.Column("kind", sa.String(length=24), nullable=False),
        sa.Column("reference_id", sa.String(length=80), nullable=False),
        sa.Column("recorded_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("entry_id"),
    )
    op.create_index("ix_ledger_entries_account_id", TABLE_NAME, ["account_id"])
    op.create_index("ix_ledger_entries_asset", TABLE_NAME, ["asset"])
    op.create_index("ix_ledger_entries_reference_id", TABLE_NAME, ["reference_id"])


def downgrade() -> None:
    op.drop_index("ix_ledger_entries_reference_id", table_name=TABLE_NAME)
    op.drop_index("ix_ledger_entries_asset", table_name=TABLE_NAME)
    op.drop_index("ix_ledger_entries_account_id", table_name=TABLE_NAME)
    op.drop_table(TABLE_NAME)
