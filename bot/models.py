from datetime import UTC, datetime, date

from sqlalchemy import (
    BigInteger,
    Boolean,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from bot.db import Base


def utcnow() -> datetime:
    return datetime.now(UTC)


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    telegram_id: Mapped[int] = mapped_column(BigInteger, unique=True, index=True)
    username: Mapped[str | None] = mapped_column(String(255))
    full_name: Mapped[str | None] = mapped_column(String(255))
    language_code: Mapped[str | None] = mapped_column(String(16))
    timezone: Mapped[str] = mapped_column(String(64), default="UTC")
    reminder_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    reminder_minutes_local: Mapped[int | None] = mapped_column(Integer)
    reminder_snoozed_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    reminder_skip_date: Mapped[date | None] = mapped_column(Date)
    reminder_last_sent_date: Mapped[date | None] = mapped_column(Date)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    decks: Mapped[list["Deck"]] = relationship(back_populates="user")


class Event(Base):
    __tablename__ = "events"

    id: Mapped[int] = mapped_column(BigInteger().with_variant(Integer, "sqlite"), primary_key=True)
    user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    name: Mapped[str] = mapped_column(String(64), index=True)
    props: Mapped[dict | None] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, index=True
    )


class Deck(Base):
    __tablename__ = "decks"
    __table_args__ = (
        UniqueConstraint("user_id", "parent_id", "name", name="uq_decks_user_parent_name"),
        Index(
            "uq_decks_user_root_name",
            "user_id",
            "name",
            unique=True,
            postgresql_where=text("parent_id IS NULL"),
            sqlite_where=text("parent_id IS NULL"),
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    parent_id: Mapped[int | None] = mapped_column(ForeignKey("decks.id", ondelete="SET NULL"))
    name: Mapped[str] = mapped_column(String(255))
    description: Mapped[str | None] = mapped_column(Text)
    source_slug: Mapped[str | None] = mapped_column(String(64), index=True)
    is_archived: Mapped[bool] = mapped_column(Boolean, default=False)
    new_cards_per_day: Mapped[int] = mapped_column(Integer, default=20)
    reviews_per_day: Mapped[int] = mapped_column(Integer, default=200)
    desired_retention: Mapped[float] = mapped_column(Float, default=0.9)
    bury_siblings: Mapped[bool] = mapped_column(Boolean, default=True)
    learning_steps_minutes: Mapped[list[int]] = mapped_column(JSONB, default=lambda: [1, 10])
    relearning_steps_minutes: Mapped[list[int]] = mapped_column(JSONB, default=lambda: [10])
    maximum_interval_days: Mapped[int] = mapped_column(Integer, default=36500)
    enable_fuzzing: Mapped[bool] = mapped_column(Boolean, default=True)
    option_preset: Mapped[str] = mapped_column(String(64), default="balanced")
    fsrs_parameters: Mapped[dict | None] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )

    user: Mapped[User] = relationship(back_populates="decks")
    parent: Mapped["Deck | None"] = relationship(remote_side=[id])
    notes: Mapped[list["Note"]] = relationship(back_populates="deck")
    cards: Mapped[list["Card"]] = relationship(back_populates="deck")


class SharedDeck(Base):
    __tablename__ = "shared_decks"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    slug: Mapped[str] = mapped_column(String(64), unique=True)
    title: Mapped[str] = mapped_column(String(255))
    description: Mapped[str] = mapped_column(Text)
    language: Mapped[str] = mapped_column(String(16))
    tags: Mapped[list[str]] = mapped_column(JSONB, default=list)
    notes: Mapped[list[dict]] = mapped_column(JSONB, default=list)
    notes_count: Mapped[int] = mapped_column(Integer)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )


class DeckShare(Base):
    __tablename__ = "deck_shares"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    deck_id: Mapped[int] = mapped_column(
        ForeignKey("decks.id", ondelete="CASCADE"), unique=True, index=True
    )
    owner_user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    token: Mapped[str] = mapped_column(String(43), unique=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Note(Base):
    __tablename__ = "notes"
    __table_args__ = (
        Index(
            "uq_notes_user_anki_guid",
            "user_id",
            "anki_guid",
            unique=True,
            postgresql_where=text("anki_guid IS NOT NULL"),
            sqlite_where=text("anki_guid IS NOT NULL"),
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    deck_id: Mapped[int] = mapped_column(ForeignKey("decks.id", ondelete="CASCADE"), index=True)
    note_type: Mapped[str] = mapped_column(String(64), default="basic")
    anki_guid: Mapped[str | None] = mapped_column(String(64))
    anki_model_id: Mapped[str | None] = mapped_column(String(64))
    fields: Mapped[dict | None] = mapped_column(JSONB)
    front: Mapped[str] = mapped_column(Text)
    back: Mapped[str] = mapped_column(Text)
    extra: Mapped[str | None] = mapped_column(Text)
    tags: Mapped[list[str]] = mapped_column(JSONB, default=list)
    source: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )

    deck: Mapped[Deck] = relationship(back_populates="notes")
    cards: Mapped[list["Card"]] = relationship(back_populates="note")


class NoteStyle(Base):
    __tablename__ = "note_styles"
    __table_args__ = (
        UniqueConstraint("user_id", "anki_model_id", name="uq_note_styles_user_model"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    anki_model_id: Mapped[str] = mapped_column(String(64))
    css: Mapped[str] = mapped_column(Text)


class Card(Base):
    __tablename__ = "cards"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    deck_id: Mapped[int] = mapped_column(ForeignKey("decks.id", ondelete="CASCADE"), index=True)
    note_id: Mapped[int] = mapped_column(ForeignKey("notes.id", ondelete="CASCADE"), index=True)
    direction: Mapped[str] = mapped_column(String(32), default="front_back")
    template_name: Mapped[str | None] = mapped_column(String(255))
    template_ord: Mapped[int] = mapped_column(Integer, default=0)
    question_template: Mapped[str | None] = mapped_column(Text)
    answer_template: Mapped[str | None] = mapped_column(Text)
    due_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    state: Mapped[str] = mapped_column(String(32), default="new", index=True)
    fsrs_data: Mapped[dict | None] = mapped_column(JSONB)
    suspended: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    buried_until: Mapped[date | None] = mapped_column(Date)
    flag: Mapped[str | None] = mapped_column(String(32))
    reps: Mapped[int] = mapped_column(Integer, default=0)
    lapses: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )

    deck: Mapped[Deck] = relationship(back_populates="cards")
    note: Mapped[Note] = relationship(back_populates="cards")
    reviews: Mapped[list["ReviewLog"]] = relationship(back_populates="card")


class ReviewLog(Base):
    __tablename__ = "review_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    deck_id: Mapped[int] = mapped_column(ForeignKey("decks.id", ondelete="CASCADE"), index=True)
    card_id: Mapped[int] = mapped_column(ForeignKey("cards.id", ondelete="CASCADE"), index=True)
    rating: Mapped[int] = mapped_column(SmallInteger)
    reviewed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    elapsed_ms: Mapped[int | None] = mapped_column(Integer)
    previous_due_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    next_due_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    fsrs_review_log: Mapped[dict | None] = mapped_column(JSONB)

    card: Mapped[Card] = relationship(back_populates="reviews")


class MediaFile(Base):
    __tablename__ = "media_files"
    __table_args__ = (
        UniqueConstraint("user_id", "original_name", "sha256", name="uq_media_user_name_hash"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    deck_id: Mapped[int | None] = mapped_column(ForeignKey("decks.id", ondelete="SET NULL"), index=True)
    original_name: Mapped[str] = mapped_column(String(512))
    content_type: Mapped[str | None] = mapped_column(String(128))
    size_bytes: Mapped[int] = mapped_column(Integer)
    sha256: Mapped[str] = mapped_column(String(64), index=True)
    content: Mapped[bytes] = mapped_column(LargeBinary)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class DailyStudyCounter(Base):
    __tablename__ = "daily_study_counters"
    __table_args__ = (
        UniqueConstraint("user_id", "deck_id", "study_date", name="uq_daily_counter"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    deck_id: Mapped[int] = mapped_column(ForeignKey("decks.id", ondelete="CASCADE"), index=True)
    study_date: Mapped[date] = mapped_column(Date, server_default=func.current_date())
    new_seen: Mapped[int] = mapped_column(Integer, default=0)
    reviews_done: Mapped[int] = mapped_column(Integer, default=0)
