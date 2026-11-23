"""message_items (structured requirements/offers per message line)

Revision ID: b7e21f4c5a90
Revises: a1c4e7d90b21
Create Date: 2026-11-23 12:00:00
"""

from collections.abc import Sequence
from typing import Union

import sqlalchemy as sa
from alembic import op

revision: str = "b7e21f4c5a90"
down_revision: Union[str, None] = "a1c4e7d90b21"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "message_items",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("message_id", sa.Integer(), nullable=False),
        sa.Column("side", sa.String(length=8), nullable=True),
        sa.Column("side_source", sa.String(length=12), nullable=True),
        sa.Column("sender_key", sa.String(length=64), nullable=False),
        sa.Column("sender_name", sa.String(length=120), nullable=True),
        sa.Column("sender_phone", sa.String(length=32), nullable=True),
        sa.Column("category", sa.String(length=16), nullable=False),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("brand", sa.String(length=60), nullable=True),
        sa.Column("model", sa.String(length=80), nullable=True),
        sa.Column("capacity_gb", sa.Integer(), nullable=True),
        sa.Column("generation", sa.String(length=8), nullable=True),
        sa.Column("speed_mhz", sa.Integer(), nullable=True),
        sa.Column("form", sa.String(length=32), nullable=True),
        sa.Column("interface", sa.String(length=16), nullable=True),
        sa.Column("health_min", sa.Integer(), nullable=True),
        sa.Column("health_max", sa.Integer(), nullable=True),
        sa.Column("condition", sa.String(length=24), nullable=True),
        sa.Column("cpu", sa.String(length=24), nullable=True),
        sa.Column("ram_gb", sa.Integer(), nullable=True),
        sa.Column("ssd_gb", sa.Integer(), nullable=True),
        sa.Column("qty", sa.Integer(), nullable=True),
        sa.Column("price_aed", sa.Float(), nullable=True),
        sa.Column("flags", sa.JSON(), nullable=True),
        sa.Column("raw_line", sa.Text(), nullable=False),
        sa.Column("item_key", sa.String(length=200), nullable=False),
        sa.Column("posted_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(),
                  server_default=sa.text("(CURRENT_TIMESTAMP)"), nullable=False),
        sa.ForeignKeyConstraint(["message_id"], ["messages.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("message_items", schema=None) as b:
        b.create_index("idx_items_message", ["message_id"], unique=False)
        b.create_index("idx_items_side_cat", ["side", "category"], unique=False)
        b.create_index("idx_items_sender", ["sender_key"], unique=False)
        b.create_index("idx_items_posted", ["posted_at"], unique=False)


def downgrade() -> None:
    with op.batch_alter_table("message_items", schema=None) as b:
        b.drop_index("idx_items_posted")
        b.drop_index("idx_items_sender")
        b.drop_index("idx_items_side_cat")
        b.drop_index("idx_items_message")
    op.drop_table("message_items")
