"""Add leech rescue state.

Revision ID: 20260821_0015
Revises: 20260821_0014
Create Date: 2026-08-21
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20260821_0015"
down_revision: Union[str, None] = "20260821_0014"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "cards",
        sa.Column(
            "review_lapses",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
        ),
    )
    op.add_column(
        "cards",
        sa.Column("leech_suspended_lapses", sa.Integer(), nullable=True),
    )
    op.create_index(
        "ix_cards_user_review_lapses",
        "cards",
        ["user_id", "review_lapses"],
        unique=False,
    )
    op.add_column(
        "review_logs",
        sa.Column("leech_alert_lapses", sa.Integer(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("review_logs", "leech_alert_lapses")
    op.drop_index("ix_cards_user_review_lapses", table_name="cards")
    op.drop_column("cards", "leech_suspended_lapses")
    op.drop_column("cards", "review_lapses")
