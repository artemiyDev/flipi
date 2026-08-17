"""Add user reminders.

Revision ID: 20260817_0009
Revises: 20260815_0008
Create Date: 2026-08-17
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20260817_0009"
down_revision: Union[str, None] = "20260815_0008"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column("reminder_enabled", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.add_column("users", sa.Column("reminder_minutes_local", sa.Integer(), nullable=True))
    op.add_column("users", sa.Column("reminder_snoozed_until", sa.DateTime(timezone=True), nullable=True))
    op.add_column("users", sa.Column("reminder_skip_date", sa.Date(), nullable=True))
    op.add_column("users", sa.Column("reminder_last_sent_date", sa.Date(), nullable=True))
    op.alter_column("users", "reminder_enabled", server_default=None)


def downgrade() -> None:
    op.drop_column("users", "reminder_last_sent_date")
    op.drop_column("users", "reminder_skip_date")
    op.drop_column("users", "reminder_snoozed_until")
    op.drop_column("users", "reminder_minutes_local")
    op.drop_column("users", "reminder_enabled")
