"""Initial schema.

Revision ID: 20260602_0001
Revises: None
Create Date: 2026-06-02
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "20260602_0001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("telegram_id", sa.BigInteger(), nullable=False),
        sa.Column("username", sa.String(length=255), nullable=True),
        sa.Column("full_name", sa.String(length=255), nullable=True),
        sa.Column("language_code", sa.String(length=16), nullable=True),
        sa.Column("timezone", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_users_telegram_id"), "users", ["telegram_id"], unique=True)

    op.create_table(
        "decks",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("parent_id", sa.Integer(), nullable=True),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("is_archived", sa.Boolean(), nullable=False),
        sa.Column("new_cards_per_day", sa.Integer(), nullable=False),
        sa.Column("reviews_per_day", sa.Integer(), nullable=False),
        sa.Column("desired_retention", sa.Float(), nullable=False),
        sa.Column("fsrs_parameters", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["parent_id"], ["decks.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", "name", name="uq_decks_user_name"),
    )
    op.create_index(op.f("ix_decks_user_id"), "decks", ["user_id"], unique=False)

    op.create_table(
        "notes",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("deck_id", sa.Integer(), nullable=False),
        sa.Column("note_type", sa.String(length=64), nullable=False),
        sa.Column("front", sa.Text(), nullable=False),
        sa.Column("back", sa.Text(), nullable=False),
        sa.Column("extra", sa.Text(), nullable=True),
        sa.Column("tags", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("source", sa.String(length=64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["deck_id"], ["decks.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_notes_deck_id"), "notes", ["deck_id"], unique=False)
    op.create_index(op.f("ix_notes_user_id"), "notes", ["user_id"], unique=False)

    op.create_table(
        "cards",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("deck_id", sa.Integer(), nullable=False),
        sa.Column("note_id", sa.Integer(), nullable=False),
        sa.Column("direction", sa.String(length=32), nullable=False),
        sa.Column("due_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("state", sa.String(length=32), nullable=False),
        sa.Column("fsrs_data", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("suspended", sa.Boolean(), nullable=False),
        sa.Column("buried_until", sa.Date(), nullable=True),
        sa.Column("flag", sa.String(length=32), nullable=True),
        sa.Column("reps", sa.Integer(), nullable=False),
        sa.Column("lapses", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["deck_id"], ["decks.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["note_id"], ["notes.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_cards_deck_id"), "cards", ["deck_id"], unique=False)
    op.create_index(op.f("ix_cards_due_at"), "cards", ["due_at"], unique=False)
    op.create_index(op.f("ix_cards_note_id"), "cards", ["note_id"], unique=False)
    op.create_index(op.f("ix_cards_state"), "cards", ["state"], unique=False)
    op.create_index(op.f("ix_cards_suspended"), "cards", ["suspended"], unique=False)
    op.create_index(op.f("ix_cards_user_id"), "cards", ["user_id"], unique=False)

    op.create_table(
        "daily_study_counters",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("deck_id", sa.Integer(), nullable=False),
        sa.Column("study_date", sa.Date(), server_default=sa.text("CURRENT_DATE"), nullable=False),
        sa.Column("new_seen", sa.Integer(), nullable=False),
        sa.Column("reviews_done", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(["deck_id"], ["decks.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", "deck_id", "study_date", name="uq_daily_counter"),
    )
    op.create_index(
        op.f("ix_daily_study_counters_deck_id"),
        "daily_study_counters",
        ["deck_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_daily_study_counters_user_id"),
        "daily_study_counters",
        ["user_id"],
        unique=False,
    )

    op.create_table(
        "review_logs",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("deck_id", sa.Integer(), nullable=False),
        sa.Column("card_id", sa.Integer(), nullable=False),
        sa.Column("rating", sa.SmallInteger(), nullable=False),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("elapsed_ms", sa.Integer(), nullable=True),
        sa.Column("previous_due_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("next_due_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("fsrs_review_log", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.ForeignKeyConstraint(["card_id"], ["cards.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["deck_id"], ["decks.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_review_logs_card_id"), "review_logs", ["card_id"], unique=False)
    op.create_index(op.f("ix_review_logs_deck_id"), "review_logs", ["deck_id"], unique=False)
    op.create_index(op.f("ix_review_logs_user_id"), "review_logs", ["user_id"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_review_logs_user_id"), table_name="review_logs")
    op.drop_index(op.f("ix_review_logs_deck_id"), table_name="review_logs")
    op.drop_index(op.f("ix_review_logs_card_id"), table_name="review_logs")
    op.drop_table("review_logs")
    op.drop_index(op.f("ix_daily_study_counters_user_id"), table_name="daily_study_counters")
    op.drop_index(op.f("ix_daily_study_counters_deck_id"), table_name="daily_study_counters")
    op.drop_table("daily_study_counters")
    op.drop_index(op.f("ix_cards_user_id"), table_name="cards")
    op.drop_index(op.f("ix_cards_suspended"), table_name="cards")
    op.drop_index(op.f("ix_cards_state"), table_name="cards")
    op.drop_index(op.f("ix_cards_note_id"), table_name="cards")
    op.drop_index(op.f("ix_cards_due_at"), table_name="cards")
    op.drop_index(op.f("ix_cards_deck_id"), table_name="cards")
    op.drop_table("cards")
    op.drop_index(op.f("ix_notes_user_id"), table_name="notes")
    op.drop_index(op.f("ix_notes_deck_id"), table_name="notes")
    op.drop_table("notes")
    op.drop_index(op.f("ix_decks_user_id"), table_name="decks")
    op.drop_table("decks")
    op.drop_index(op.f("ix_users_telegram_id"), table_name="users")
    op.drop_table("users")
