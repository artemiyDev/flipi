"""Scope deck names to their parent.

Revision ID: 20260815_0006
Revises: 20260602_0005
Create Date: 2026-08-15
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20260815_0006"
down_revision: Union[str, None] = "20260602_0005"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.drop_constraint("uq_decks_user_name", "decks", type_="unique")
    op.create_unique_constraint(
        "uq_decks_user_parent_name",
        "decks",
        ["user_id", "parent_id", "name"],
    )
    op.create_index(
        "uq_decks_user_root_name",
        "decks",
        ["user_id", "name"],
        unique=True,
        postgresql_where=sa.text("parent_id IS NULL"),
    )


def downgrade() -> None:
    op.drop_index("uq_decks_user_root_name", table_name="decks")
    op.drop_constraint("uq_decks_user_parent_name", "decks", type_="unique")
    op.create_unique_constraint("uq_decks_user_name", "decks", ["user_id", "name"])
