"""Create the append-only quantitative evidence table.

Revision ID: 0016_quant_evidence
Revises: 0015_decision_memory
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0016_quant_evidence"
down_revision: str | None = "0015_decision_memory"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    if sa.inspect(op.get_bind()).has_table("quant_evidence"):
        return
    op.create_table(
        "quant_evidence",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("instrument_id", sa.String(160), nullable=False),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
    )
    op.create_index("ix_quant_evidence_instrument_id", "quant_evidence", ["instrument_id"])
    op.create_index("ix_quant_evidence_observed_at", "quant_evidence", ["observed_at"])


def downgrade() -> None:
    # Preserve evidence when the table predates this migration through init_database.
    return None
