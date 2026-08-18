"""Add FSRS optimization timestamp.

Revision ID: 20260818_0013
Revises: 20260817_0012
Create Date: 2026-08-18
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20260818_0013"
down_revision: Union[str, None] = "20260817_0012"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("decks", sa.Column("fsrs_optimized_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column("decks", "fsrs_optimized_at")
