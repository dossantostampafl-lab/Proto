"""create durable OMS state: consumed permits, orders, equity checkpoints

Revision ID: 0009_oms_state
Revises: 0008_ledger_entries
Create Date: 2026-10-14
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0009_oms_state"
down_revision: str | None = "0008_ledger_entries"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLE_NAMES = ("oms_consumed_permits", "oms_orders", "oms_equity_checkpoints")


def upgrade() -> None:
    op.create_table(
        "oms_consumed_permits",
        sa.Column("permit_id", sa.String(length=36), nullable=False),
        sa.Column("intent_id", sa.String(length=36), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("consumed_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("permit_id"),
    )
    op.create_index("ix_oms_consumed_permits_intent_id", "oms_consumed_permits", ["intent_id"])
    op.create_table(
        "oms_orders",
        sa.Column("client_order_id", sa.String(length=36), nullable=False),
        sa.Column("account_id", sa.String(length=80), nullable=False),
        sa.Column("intent_id", sa.String(length=36), nullable=False),
        sa.Column("intent", sa.Text(), nullable=False),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column("reserved_asset", sa.String(length=20), nullable=False),
        sa.Column("reserved", sa.String(length=64), nullable=False),
        sa.Column("released", sa.String(length=64), nullable=False),
        sa.Column("filled_quantity", sa.String(length=64), nullable=False),
        sa.Column("filled_notional", sa.String(length=64), nullable=False),
        sa.Column("fee", sa.String(length=64), nullable=False),
        sa.Column("venue_order_id", sa.String(length=80), nullable=True),
        sa.Column("reason", sa.String(length=200), nullable=True),
        sa.Column("history", sa.Text(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("client_order_id"),
        sa.UniqueConstraint("intent_id"),
    )
    op.create_index("ix_oms_orders_account_id", "oms_orders", ["account_id"])
    op.create_table(
        "oms_equity_checkpoints",
        sa.Column("account_id", sa.String(length=80), nullable=False),
        sa.Column("quote_asset", sa.String(length=20), nullable=False),
        sa.Column("day", sa.Date(), nullable=False),
        sa.Column("start_of_day_equity", sa.String(length=64), nullable=False),
        sa.Column("peak_equity", sa.String(length=64), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("account_id", "quote_asset"),
    )


def downgrade() -> None:
    op.drop_table("oms_equity_checkpoints")
    op.drop_index("ix_oms_orders_account_id", table_name="oms_orders")
    op.drop_table("oms_orders")
    op.drop_index("ix_oms_consumed_permits_intent_id", table_name="oms_consumed_permits")
    op.drop_table("oms_consumed_permits")
