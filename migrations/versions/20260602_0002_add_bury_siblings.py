"""Add bury siblings deck option.

Revision ID: 20260602_0002
Revises: 20260602_0001
Create Date: 2026-06-02
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20260602_0002"
down_revision: Union[str, None] = "20260602_0001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "decks",
        sa.Column("bury_siblings", sa.Boolean(), nullable=False, server_default=sa.true()),
    )
    op.alter_column("decks", "bury_siblings", server_default=None)


def downgrade() -> None:
    op.drop_column("decks", "bury_siblings")
