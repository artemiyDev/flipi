"""Add Anki field and template snapshots.

Revision ID: 20260602_0004
Revises: 20260602_0003
Create Date: 2026-06-02
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "20260602_0004"
down_revision: Union[str, None] = "20260602_0003"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("notes", sa.Column("anki_model_id", sa.String(length=64), nullable=True))
    op.add_column("notes", sa.Column("fields", postgresql.JSONB(astext_type=sa.Text()), nullable=True))
    op.add_column("cards", sa.Column("template_name", sa.String(length=255), nullable=True))
    op.add_column("cards", sa.Column("template_ord", sa.Integer(), nullable=False, server_default="0"))
    op.add_column("cards", sa.Column("question_template", sa.Text(), nullable=True))
    op.add_column("cards", sa.Column("answer_template", sa.Text(), nullable=True))
    op.alter_column("cards", "template_ord", server_default=None)


def downgrade() -> None:
    op.drop_column("cards", "answer_template")
    op.drop_column("cards", "question_template")
    op.drop_column("cards", "template_ord")
    op.drop_column("cards", "template_name")
    op.drop_column("notes", "fields")
    op.drop_column("notes", "anki_model_id")
