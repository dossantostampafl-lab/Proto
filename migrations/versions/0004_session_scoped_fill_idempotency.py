"""scope simulation fill idempotency to session

Revision ID: 0004_session_scoped_fill_idempotency
Revises: 0003_simulation_sessions
Create Date: 2026-08-31
"""
from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0004_session_scoped_fill_idempotency"
down_revision: str | None = "0003_simulation_sessions"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_constraint(
        "simulation_fills_order_id_key",
        "simulation_fills",
        type_="unique",
    )
    op.create_unique_constraint(
        "uq_simulation_fills_session_order",
        "simulation_fills",
        ["session_id", "order_id"],
    )


def downgrade() -> None:
    op.drop_constraint(
        "uq_simulation_fills_session_order",
        "simulation_fills",
        type_="unique",
    )
    op.create_unique_constraint(
        "simulation_fills_order_id_key",
        "simulation_fills",
        ["order_id"],
    )
