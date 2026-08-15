from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from bot.models import Deck, User
from bot.services.apkg_importer import ImportedCard, ImportedMedia, ImportedNote
from bot.services.cards import create_basic_note, import_anki_note, note_exists
from bot.services.decks import deck_full_path, get_deck, list_all_user_decks, resolve_apkg_deck
from bot.services.media import save_imported_media_files


class ImportFlowError(ValueError):
    """Raised when an import payload cannot be imported."""


@dataclass(frozen=True)
class ImportResult:
    added: int
    updated: int
    unchanged: int
    decks_created: list[str]
    media_saved: int
    media_skipped: int
    added_cards: int


async def import_text_cards(
    session: AsyncSession,
    user: User,
    deck_id: int,
    rows: list[ImportedCard],
    source: str,
) -> ImportResult:
    if not rows:
        raise ImportFlowError(
            "Не удалось найти карточки. Нужны минимум две колонки: вопрос и ответ."
        )

    deck = await get_deck(session, user, deck_id)
    if deck is None:
        raise LookupError("Колода не найдена.")

    added = 0
    unchanged = 0
    for row in rows:
        if await note_exists(session, user, deck, row.front, row.back):
            unchanged += 1
            continue
        await create_basic_note(
            session=session,
            user=user,
            deck=deck,
            front=row.front,
            back=row.back,
            tags=row.tags,
            create_reverse=row.create_reverse,
            note_type=row.note_type,
            anki_model_id=row.anki_model_id,
            fields=row.fields,
            template_name=row.template_name,
            template_ord=row.template_ord,
            question_template=row.question_template,
            answer_template=row.answer_template,
            source=source,
        )
        added += 1

    return ImportResult(added, 0, unchanged, [], 0, 0, added)


async def import_apkg_notes(
    session: AsyncSession,
    user: User,
    deck_id: int | None,
    notes: list[ImportedNote],
    media_files: list[ImportedMedia],
    source: str,
) -> ImportResult:
    if not notes:
        raise ImportFlowError("Не удалось найти заметки в APKG.")

    auto_decks = deck_id is None
    selected_deck = None
    if not auto_decks:
        selected_deck = await get_deck(session, user, deck_id)
        if selected_deck is None:
            raise LookupError("Колода не найдена.")

    before_deck_ids = {deck.id for deck in await list_all_user_decks(session, user)}
    deck_cache: dict[str, Deck] = {}
    added = updated = unchanged = added_cards = media_saved = media_skipped = 0
    media_saved_once = False

    for note in notes:
        note_deck = selected_deck
        if auto_decks:
            note_deck = await _get_cached_deck(
                session, user, deck_cache, note.deck_name or "Imported APKG"
            )

        card_specs = []
        for card in note.cards:
            card_deck = note_deck
            if auto_decks and card.deck_name:
                card_deck = await _get_cached_deck(session, user, deck_cache, card.deck_name)
            card_specs.append(
                {
                    "deck": card_deck,
                    "direction": "front_back",
                    "template_name": card.template_name,
                    "template_ord": card.template_ord,
                    "question_template": card.question_template,
                    "answer_template": card.answer_template,
                }
            )

        result = await import_anki_note(
            session=session,
            user=user,
            deck=note_deck,
            front=note.front,
            back=note.back,
            extra=note.extra,
            tags=note.tags,
            note_type=note.note_type,
            anki_guid=note.guid,
            anki_model_id=note.anki_model_id,
            fields=note.fields,
            source=source,
            card_specs=card_specs,
        )
        if result.status == "added":
            added += 1
        elif result.status == "updated":
            updated += 1
        else:
            unchanged += 1
        added_cards += result.added_cards

        if auto_decks and media_files and not media_saved_once:
            saved, skipped = await save_imported_media_files(session, user, None, media_files)
            media_saved += saved
            media_skipped += skipped
            media_saved_once = True
            await session.commit()

    if not auto_decks and selected_deck is not None and media_files:
        saved, skipped = await save_imported_media_files(session, user, selected_deck, media_files)
        media_saved += saved
        media_skipped += skipped
        await session.commit()

    decks = await list_all_user_decks(session, user)
    decks_by_id = {deck.id: deck for deck in decks}
    decks_created = [
        deck_full_path(deck, decks_by_id) for deck in decks if deck.id not in before_deck_ids
    ]
    return ImportResult(
        added,
        updated,
        unchanged,
        decks_created,
        media_saved,
        media_skipped,
        added_cards,
    )


async def _get_cached_deck(
    session: AsyncSession,
    user: User,
    cache: dict[str, Deck],
    name: str,
) -> Deck:
    deck = cache.get(name)
    if deck is None:
        deck = await resolve_apkg_deck(session, user, name, "Imported from APKG")
        cache[name] = deck
    return deck
