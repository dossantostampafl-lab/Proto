"""Private conversation history for opt-in local ChatGPT explanations."""

import sqlalchemy as sa
from alembic import op

revision = "0014_agent_conversations"
down_revision = "0013_control_state"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "agent_conversations",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("owner", sa.String(64), nullable=False),
        sa.Column("agent_id", sa.String(160), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("messages", sa.Text(), nullable=False),
    )
    op.create_index("ix_agent_conversations_owner", "agent_conversations", ["owner"])


def downgrade():
    op.drop_table("agent_conversations")
