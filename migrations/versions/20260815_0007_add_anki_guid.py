"""Add Anki note guid.

Revision ID: 20260815_0007
Revises: 20260815_0006
Create Date: 2026-08-15
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20260815_0007"
down_revision: Union[str, None] = "20260815_0006"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("notes", sa.Column("anki_guid", sa.String(length=64), nullable=True))
    op.create_index(
        "uq_notes_user_anki_guid",
        "notes",
        ["user_id", "anki_guid"],
        unique=True,
        postgresql_where=sa.text("anki_guid IS NOT NULL"),
        sqlite_where=sa.text("anki_guid IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index("uq_notes_user_anki_guid", table_name="notes")
    op.drop_column("notes", "anki_guid")
