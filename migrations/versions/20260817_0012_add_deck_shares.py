"""Add deck shares.

Revision ID: 20260817_0012
Revises: 20260817_0011
Create Date: 2026-08-17
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20260817_0012"
down_revision: Union[str, None] = "20260817_0011"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "deck_shares",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("deck_id", sa.Integer(), nullable=False),
        sa.Column("owner_user_id", sa.Integer(), nullable=False),
        sa.Column("token", sa.String(length=43), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["deck_id"], ["decks.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["owner_user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("deck_id"),
        sa.UniqueConstraint("token"),
    )
    op.create_index(op.f("ix_deck_shares_deck_id"), "deck_shares", ["deck_id"], unique=False)
    op.create_index(
        op.f("ix_deck_shares_owner_user_id"), "deck_shares", ["owner_user_id"], unique=False
    )
    op.create_index(op.f("ix_deck_shares_token"), "deck_shares", ["token"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_deck_shares_token"), table_name="deck_shares")
    op.drop_index(op.f("ix_deck_shares_owner_user_id"), table_name="deck_shares")
    op.drop_index(op.f("ix_deck_shares_deck_id"), table_name="deck_shares")
    op.drop_table("deck_shares")
