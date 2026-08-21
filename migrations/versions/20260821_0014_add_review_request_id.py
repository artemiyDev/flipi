"""Add idempotency data to review logs.

Revision ID: 20260821_0014
Revises: 20260818_0013
Create Date: 2026-08-21
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20260821_0014"
down_revision: Union[str, None] = "20260818_0013"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "review_logs",
        sa.Column("request_id", sa.String(length=64), nullable=True),
    )
    op.add_column(
        "review_logs",
        sa.Column("state_after", sa.String(length=32), nullable=True),
    )
    op.create_index(
        "uq_review_logs_user_request_id",
        "review_logs",
        ["user_id", "request_id"],
        unique=True,
        postgresql_where=sa.text("request_id IS NOT NULL"),
        sqlite_where=sa.text("request_id IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index("uq_review_logs_user_request_id", table_name="review_logs")
    op.drop_column("review_logs", "state_after")
    op.drop_column("review_logs", "request_id")
