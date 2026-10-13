"""Create paper OMS, fill-ledger, reservations and reconciliation guard.

Revision ID: 0009_execution_oms
Revises: 0008_execution_consumed_permits
Create Date: 2026-10-13
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0009_execution_oms"
down_revision: str | None = "0008_execution_consumed_permits"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "execution_orders",
        sa.Column("client_order_id", sa.String(36), primary_key=True),
        sa.Column("intent_id", sa.String(36), nullable=False, unique=True),
        sa.Column("intent_fingerprint", sa.String(64), nullable=False),
        sa.Column("permit_id", sa.String(36), nullable=False, unique=True),
        sa.Column("venue", sa.String(40), nullable=False),
        sa.Column("instrument_id", sa.String(160), nullable=False),
        sa.Column("side", sa.String(4), nullable=False),
        sa.Column("quantity", sa.String(80), nullable=False),
        sa.Column("order_type", sa.String(16), nullable=False),
        sa.Column("limit_price", sa.String(80), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("filled_quantity", sa.String(80), nullable=False),
        sa.Column("average_price", sa.String(80), nullable=True),
        sa.Column("cumulative_notional", sa.String(80), nullable=False),
        sa.Column("fee", sa.String(80), nullable=False),
        sa.Column("fee_asset", sa.String(20), nullable=True),
        sa.Column("venue_order_id", sa.String(80), nullable=True),
        sa.Column("reason", sa.String(200), nullable=True),
        sa.Column("reported_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "execution_fills",
        sa.Column("id", sa.Integer(), autoincrement=True, primary_key=True),
        sa.Column("client_order_id", sa.String(36), nullable=False),
        sa.Column("venue_order_id", sa.String(80), nullable=True),
        sa.Column("delta_quantity", sa.String(80), nullable=False),
        sa.Column("cumulative_quantity", sa.String(80), nullable=False),
        sa.Column("delta_notional", sa.String(80), nullable=False),
        sa.Column("delta_fee", sa.String(80), nullable=False),
        sa.Column("fee_asset", sa.String(20), nullable=True),
        sa.Column("reported_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["client_order_id"], ["execution_orders.client_order_id"]
        ),
        sa.UniqueConstraint(
            "client_order_id",
            "cumulative_quantity",
            name="uq_execution_fill_cumulative",
        ),
    )
    op.create_table(
        "execution_balance_entries",
        sa.Column("id", sa.Integer(), autoincrement=True, primary_key=True),
        sa.Column("client_order_id", sa.String(36), nullable=False),
        sa.Column("venue", sa.String(40), nullable=False),
        sa.Column("asset", sa.String(20), nullable=False),
        sa.Column("amount", sa.String(80), nullable=False),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("recorded_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["client_order_id"], ["execution_orders.client_order_id"]
        ),
    )
    op.create_table(
        "execution_opening_balances",
        sa.Column("venue", sa.String(40), primary_key=True),
        sa.Column("asset", sa.String(20), primary_key=True),
        sa.Column("balance", sa.String(80), nullable=False),
    )
    op.create_table(
        "execution_reservations",
        sa.Column("client_order_id", sa.String(36), primary_key=True),
        sa.Column("venue", sa.String(40), nullable=False),
        sa.Column("asset", sa.String(20), nullable=False),
        sa.Column("total", sa.String(80), nullable=False),
        sa.Column("remaining", sa.String(80), nullable=False),
        sa.ForeignKeyConstraint(
            ["client_order_id"], ["execution_orders.client_order_id"]
        ),
    )
    op.create_table(
        "execution_equity_checkpoints",
        sa.Column("venue", sa.String(40), primary_key=True),
        sa.Column("day", sa.String(10), nullable=False),
        sa.Column("start_of_day_equity", sa.String(80), nullable=False),
        sa.Column("peak_equity", sa.String(80), nullable=False),
    )
    op.create_table(
        "execution_venue_halts",
        sa.Column("venue", sa.String(40), primary_key=True),
        sa.Column("reason", sa.String(200), nullable=False),
        sa.Column("halted_at", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("execution_venue_halts")
    op.drop_table("execution_equity_checkpoints")
    op.drop_table("execution_reservations")
    op.drop_table("execution_opening_balances")
    op.drop_table("execution_balance_entries")
    op.drop_table("execution_fills")
    op.drop_table("execution_orders")
