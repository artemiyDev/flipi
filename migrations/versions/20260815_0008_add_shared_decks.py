"""Add shared deck catalog.

Revision ID: 20260815_0008
Revises: 20260815_0007
Create Date: 2026-08-15
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "20260815_0008"
down_revision: Union[str, None] = "20260815_0007"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "shared_decks",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("slug", sa.String(length=64), nullable=False),
        sa.Column("title", sa.String(length=255), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("language", sa.String(length=16), nullable=False),
        sa.Column("tags", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("notes", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("notes_count", sa.Integer(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("slug"),
    )
    op.add_column("decks", sa.Column("source_slug", sa.String(length=64), nullable=True))
    op.create_index("ix_decks_source_slug", "decks", ["source_slug"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_decks_source_slug", table_name="decks")
    op.drop_column("decks", "source_slug")
    op.drop_table("shared_decks")
