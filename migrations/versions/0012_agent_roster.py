"""create the trader-agent roster and its order attribution

Revision ID: 0012_agent_roster
Revises: 0011_live_tick_sequence_bigint
Create Date: 2026-10-17
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0012_agent_roster"
down_revision: str | None = "0011_live_tick_sequence_bigint"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

AGENTS = "agent_roster"
ORDERS = "agent_orders"
# Decimals are stored as their exact text (see services/oms/store.py).
_DECIMAL = sa.String(length=64)


def upgrade() -> None:
    op.create_table(
        AGENTS,
        sa.Column("agent_id", sa.String(length=40), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=40), nullable=False),
        sa.Column("slot_kind", sa.String(length=40), nullable=False),
        sa.Column("instrument_id", sa.String(length=160), nullable=False),
        sa.Column("params", sa.Text(), nullable=False),
        sa.Column("capital_brl", _DECIMAL, nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("generation", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("eliminated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("elimination_reason", sa.String(length=200), nullable=True),
        sa.Column("position_quantity", _DECIMAL, nullable=False),
        sa.Column("position_cost", _DECIMAL, nullable=False),
        sa.Column("realized_pnl", _DECIMAL, nullable=False),
        sa.Column("fees", _DECIMAL, nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("agent_id"),
        sa.UniqueConstraint("sequence", name="uq_agent_roster_sequence"),
    )
    op.create_index("ix_agent_roster_instrument_id", AGENTS, ["instrument_id"])
    op.create_table(
        ORDERS,
        sa.Column("intent_id", sa.String(length=36), nullable=False),
        sa.Column("agent_id", sa.String(length=40), nullable=False),
        sa.Column("client_order_id", sa.String(length=80), nullable=True),
        sa.Column("instrument_id", sa.String(length=160), nullable=False),
        sa.Column("side", sa.String(length=4), nullable=False),
        sa.Column("applied_quantity", _DECIMAL, nullable=False),
        sa.Column("applied_notional", _DECIMAL, nullable=False),
        sa.Column("applied_fee", _DECIMAL, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("intent_id"),
    )
    op.create_index("ix_agent_orders_agent_id", ORDERS, ["agent_id"])


def downgrade() -> None:
    op.drop_index("ix_agent_orders_agent_id", table_name=ORDERS)
    op.drop_table(ORDERS)
    op.drop_index("ix_agent_roster_instrument_id", table_name=AGENTS)
    op.drop_table(AGENTS)
