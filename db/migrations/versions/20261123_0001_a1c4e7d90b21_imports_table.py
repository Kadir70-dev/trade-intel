"""imports table (uploader audit trail)

Revision ID: a1c4e7d90b21
Revises: 1f55ca15e963
Create Date: 2026-11-23 10:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "a1c4e7d90b21"
down_revision: str | None = "1f55ca15e963"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "imports",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("uploader", sa.String(length=64), nullable=False),
        sa.Column("filename", sa.String(length=255), nullable=False),
        sa.Column(
            "source_format",
            sa.Enum("whatsapp", "csv", "excel", "manual",
                    name="sourcetype", native_enum=False, length=16,
                    validate_strings=True),
            nullable=False,
        ),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("file_sha256", sa.String(length=64), nullable=True),
        sa.Column("bytes_size", sa.Integer(), nullable=False),
        sa.Column("messages_seen", sa.Integer(), nullable=False),
        sa.Column("messages_inserted", sa.Integer(), nullable=False),
        sa.Column("messages_skipped_dup", sa.Integer(), nullable=False),
        sa.Column("messages_skipped_other", sa.Integer(), nullable=False),
        sa.Column("inventory_inserted", sa.Integer(), nullable=False),
        sa.Column("demand_inserted", sa.Integer(), nullable=False),
        sa.Column("matches_written", sa.Integer(), nullable=False),
        sa.Column("errors", sa.Integer(), nullable=False),
        sa.Column("error_detail", sa.Text(), nullable=True),
        sa.Column("finished_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(),
                  server_default=sa.text("(CURRENT_TIMESTAMP)"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("imports", schema=None) as batch_op:
        batch_op.create_index("idx_imports_created", ["created_at"], unique=False)


def downgrade() -> None:
    with op.batch_alter_table("imports", schema=None) as batch_op:
        batch_op.drop_index("idx_imports_created")
    op.drop_table("imports")
