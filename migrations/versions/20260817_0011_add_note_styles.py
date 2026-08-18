"""Add note styles.

Revision ID: 20260817_0011
Revises: 20260817_0010
Create Date: 2026-08-17
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20260817_0011"
down_revision: Union[str, None] = "20260817_0010"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "note_styles",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("anki_model_id", sa.String(length=64), nullable=False),
        sa.Column("css", sa.Text(), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", "anki_model_id", name="uq_note_styles_user_model"),
    )
    op.create_index(op.f("ix_note_styles_user_id"), "note_styles", ["user_id"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_note_styles_user_id"), table_name="note_styles")
    op.drop_table("note_styles")
