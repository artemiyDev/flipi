import secrets
from dataclasses import dataclass

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from bot.models import Card, Deck, DeckShare, Note, NoteStyle, User
from bot.services.decks import available_root_deck_name
from bot.services.scheduler import new_fsrs_card_json


class ShareAlreadyInstalledError(ValueError):
    """Raised when a shared deck is already active for a user."""


class ShareOwnDeckError(ValueError):
    """Raised when a user tries to install their own shared deck."""


@dataclass(frozen=True)
class ShareRecord:
    share: DeckShare
    deck: Deck
    owner: User


@dataclass(frozen=True)
class ShareInstallResult:
    deck_id: int
    added: int
    owner_user_id: int


async def create_or_get_share(session: AsyncSession, deck: Deck, user: User) -> tuple[DeckShare, bool]:
    share = await session.scalar(select(DeckShare).where(DeckShare.deck_id == deck.id))
    if share is not None:
        return share, False

    for _ in range(3):
        share = DeckShare(
            deck_id=deck.id,
            owner_user_id=user.id,
            token=secrets.token_urlsafe(16),
        )
        try:
            async with session.begin_nested():
                session.add(share)
                await session.flush()
        except IntegrityError:
            existing = await session.scalar(
                select(DeckShare).where(DeckShare.deck_id == deck.id)
            )
            if existing is not None:
                return existing, False
            continue
        return share, True
    raise RuntimeError("Unable to create deck share")


async def get_share_record(session: AsyncSession, token: str) -> ShareRecord | None:
    result = await session.execute(
        select(DeckShare, Deck, User)
        .join(Deck, DeckShare.deck_id == Deck.id)
        .join(User, DeckShare.owner_user_id == User.id)
        .where(DeckShare.token == token)
    )
    row = result.one_or_none()
    if row is None:
        return None
    return ShareRecord(share=row[0], deck=row[1], owner=row[2])


async def share_preview(session: AsyncSession, user: User, token: str) -> dict | None:
    record = await get_share_record(session, token)
    if record is None:
        return None

    installed = await session.scalar(
        select(Deck.id).where(
            Deck.user_id == user.id,
            Deck.is_archived.is_(False),
            Deck.source_slug == f"share:{token}",
        )
    )
    cards_count = await session.scalar(select(func.count(Card.id)).where(Card.deck_id == record.deck.id))
    return {
        "title": record.deck.name,
        "description": record.deck.description,
        "cards_count": cards_count or 0,
        "author": record.owner.full_name or record.owner.username or "Unknown author",
        "installed": installed is not None,
        "own": record.owner.id == user.id,
    }


async def install_shared_deck(
    session: AsyncSession,
    user: User,
    token: str,
) -> ShareInstallResult | None:
    record = await get_share_record(session, token)
    if record is None:
        return None
    if record.owner.id == user.id:
        raise ShareOwnDeckError("Cannot install own shared deck")

    existing_install = await session.scalar(
        select(Deck.id).where(
            Deck.user_id == user.id,
            Deck.is_archived.is_(False),
            Deck.source_slug == f"share:{token}",
        )
    )
    if existing_install is not None:
        raise ShareAlreadyInstalledError("Shared deck is already installed")

    deck_name = await available_root_deck_name(session, user, record.deck.name, "share")
    copied_deck = Deck(
        user_id=user.id,
        name=deck_name,
        description=record.deck.description,
        source_slug=f"share:{token}",
    )
    session.add(copied_deck)
    await session.flush()

    source_notes = list(
        (
            await session.scalars(
                select(Note)
                .where(Note.deck_id == record.deck.id)
                .options(selectinload(Note.cards))
                .order_by(Note.id)
            )
        ).all()
    )
    await _copy_note_styles(session, record.owner, user, source_notes)
    for source_note in source_notes:
        copied_note = Note(
            user_id=user.id,
            deck_id=copied_deck.id,
            note_type=source_note.note_type,
            anki_model_id=source_note.anki_model_id,
            fields=dict(source_note.fields) if source_note.fields is not None else None,
            front=source_note.front,
            back=source_note.back,
            extra=source_note.extra,
            tags=list(source_note.tags),
            source=source_note.source,
        )
        session.add(copied_note)
        await session.flush()
        for source_card in source_note.cards:
            session.add(
                Card(
                    user_id=user.id,
                    deck_id=copied_deck.id,
                    note_id=copied_note.id,
                    direction=source_card.direction,
                    template_name=source_card.template_name,
                    template_ord=source_card.template_ord,
                    question_template=source_card.question_template,
                    answer_template=source_card.answer_template,
                    fsrs_data=new_fsrs_card_json(),
                )
            )
    await session.flush()
    return ShareInstallResult(
        deck_id=copied_deck.id,
        added=len(source_notes),
        owner_user_id=record.owner.id,
    )


async def _copy_note_styles(
    session: AsyncSession,
    owner: User,
    recipient: User,
    notes: list[Note],
) -> None:
    model_ids = {note.anki_model_id for note in notes if note.anki_model_id is not None}
    if not model_ids:
        return
    styles = list(
        (
            await session.scalars(
                select(NoteStyle).where(
                    NoteStyle.user_id == owner.id,
                    NoteStyle.anki_model_id.in_(model_ids),
                )
            )
        ).all()
    )
    existing_model_ids = set(
        (
            await session.scalars(
                select(NoteStyle.anki_model_id).where(
                    NoteStyle.user_id == recipient.id,
                    NoteStyle.anki_model_id.in_(model_ids),
                )
            )
        ).all()
    )
    for style in styles:
        if style.anki_model_id not in existing_model_ids:
            session.add(
                NoteStyle(
                    user_id=recipient.id,
                    anki_model_id=style.anki_model_id,
                    css=style.css,
                )
            )
