"""Add deck scheduler options.

Revision ID: 20260602_0005
Revises: 20260602_0004
Create Date: 2026-06-02
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "20260602_0005"
down_revision: Union[str, None] = "20260602_0004"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "decks",
        sa.Column(
            "learning_steps_minutes",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'[1, 10]'::jsonb"),
        ),
    )
    op.add_column(
        "decks",
        sa.Column(
            "relearning_steps_minutes",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'[10]'::jsonb"),
        ),
    )
    op.add_column(
        "decks",
        sa.Column("maximum_interval_days", sa.Integer(), nullable=False, server_default="36500"),
    )
    op.add_column(
        "decks",
        sa.Column("enable_fuzzing", sa.Boolean(), nullable=False, server_default=sa.true()),
    )
    op.add_column(
        "decks",
        sa.Column("option_preset", sa.String(length=64), nullable=False, server_default="balanced"),
    )
    op.alter_column("decks", "learning_steps_minutes", server_default=None)
    op.alter_column("decks", "relearning_steps_minutes", server_default=None)
    op.alter_column("decks", "maximum_interval_days", server_default=None)
    op.alter_column("decks", "enable_fuzzing", server_default=None)
    op.alter_column("decks", "option_preset", server_default=None)


def downgrade() -> None:
    op.drop_column("decks", "option_preset")
    op.drop_column("decks", "enable_fuzzing")
    op.drop_column("decks", "maximum_interval_days")
    op.drop_column("decks", "relearning_steps_minutes")
    op.drop_column("decks", "learning_steps_minutes")
