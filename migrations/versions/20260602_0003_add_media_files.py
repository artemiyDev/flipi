"""Add media files.

Revision ID: 20260602_0003
Revises: 20260602_0002
Create Date: 2026-06-02
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20260602_0003"
down_revision: Union[str, None] = "20260602_0002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "media_files",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("deck_id", sa.Integer(), nullable=True),
        sa.Column("original_name", sa.String(length=512), nullable=False),
        sa.Column("content_type", sa.String(length=128), nullable=True),
        sa.Column("size_bytes", sa.Integer(), nullable=False),
        sa.Column("sha256", sa.String(length=64), nullable=False),
        sa.Column("content", sa.LargeBinary(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["deck_id"], ["decks.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", "original_name", "sha256", name="uq_media_user_name_hash"),
    )
    op.create_index(op.f("ix_media_files_deck_id"), "media_files", ["deck_id"], unique=False)
    op.create_index(op.f("ix_media_files_sha256"), "media_files", ["sha256"], unique=False)
    op.create_index(op.f("ix_media_files_user_id"), "media_files", ["user_id"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_media_files_user_id"), table_name="media_files")
    op.drop_index(op.f("ix_media_files_sha256"), table_name="media_files")
    op.drop_index(op.f("ix_media_files_deck_id"), table_name="media_files")
    op.drop_table("media_files")
