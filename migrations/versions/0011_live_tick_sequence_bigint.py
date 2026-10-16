"""widen live_market_ticks.sequence to BIGINT

Binance sequences are epoch milliseconds and overflow a 32-bit INTEGER.

Revision ID: 0011_live_tick_sequence_bigint
Revises: 0010_promotion_scopes
Create Date: 2026-10-16
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0011_live_tick_sequence_bigint"
down_revision: str | None = "0010_promotion_scopes"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("live_market_ticks") as batch:
        batch.alter_column("sequence", type_=sa.BigInteger(), existing_type=sa.Integer())


def downgrade() -> None:
    with op.batch_alter_table("live_market_ticks") as batch:
        batch.alter_column("sequence", type_=sa.Integer(), existing_type=sa.BigInteger())
