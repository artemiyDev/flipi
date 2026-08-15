from io import BytesIO

from aiogram import F, Bot, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from bot.db import async_session
from bot.keyboards import choose_import_deck
from bot.services.cards import create_basic_note, import_anki_note, note_exists
from bot.services.apkg_importer import ImportedCard, ImportedNote, parse_apkg_media, parse_apkg_notes
from bot.services.decks import (
    get_deck,
    get_or_create_deck,
    list_user_deck_display_choices,
    list_user_decks,
    resolve_apkg_deck,
)
from bot.services.importers import decode_text_payload, parse_text_cards
from bot.services.media import save_imported_media_files
from bot.services.users import get_or_create_user
from bot.states import ImportCards

router = Router()
MAX_IMPORT_BYTES = 20 * 1024 * 1024


@router.callback_query(F.data == "import:start")
async def import_choose_deck(callback: CallbackQuery, state: FSMContext) -> None:
    await callback.answer()
    if callback.from_user is None or callback.message is None:
        return

    async with async_session() as session:
        user = await get_or_create_user(session, callback.from_user)
        deck_choices = await list_user_deck_display_choices(session, user)

    if not deck_choices:
        await callback.message.answer("Сначала создайте колоду.")
        return

    await state.set_state(ImportCards.deck_id)
    await callback.message.answer(
        "Выберите колоду для импорта.",
        reply_markup=choose_import_deck(deck_choices),
    )


@router.callback_query(F.data == "import:apkg_auto")
async def import_wait_apkg_auto(callback: CallbackQuery, state: FSMContext) -> None:
    await callback.answer()
    if callback.message is None:
        return
    await state.update_data(deck_id="auto")
    await state.set_state(ImportCards.payload)
    await callback.message.answer("Отправьте APKG-файл. Колоды будут созданы из структуры пакета.")


@router.callback_query(F.data.startswith("import:deck:"))
async def import_wait_payload(callback: CallbackQuery, state: FSMContext) -> None:
    await callback.answer()
    if callback.message is None or callback.data is None:
        return
    deck_id = int(callback.data.split(":")[-1])
    await state.update_data(deck_id=deck_id)
    await state.set_state(ImportCards.payload)
    await callback.message.answer(
        "Отправьте CSV, TSV, TXT или вставьте строки сообщением.\n"
        "Формат: вопрос<TAB>ответ<TAB>теги<TAB>reverse."
    )


@router.message(ImportCards.payload, F.document)
async def import_document(message: Message, state: FSMContext, bot: Bot) -> None:
    if message.document is None:
        return
    if message.document.file_size and message.document.file_size > MAX_IMPORT_BYTES:
        await message.answer("Файл слишком большой. Текущий лимит импорта: 20 MB.")
        return
    suffix = (message.document.file_name or "").lower().rsplit(".", 1)[-1]
    if suffix not in {"csv", "tsv", "txt", "apkg"}:
        await message.answer("Сейчас поддерживаются CSV, TSV, TXT и базовый APKG.")
        return
    data = await state.get_data()
    if data.get("deck_id") == "auto" and suffix != "apkg":
        await message.answer("Автоматическое создание колод доступно только для APKG.")
        return

    buffer = BytesIO()
    await bot.download(message.document, destination=buffer)
    if suffix == "apkg":
        try:
            package_payload = buffer.getvalue()
            notes = parse_apkg_notes(package_payload)
            media_files = parse_apkg_media(package_payload)
        except Exception as exc:
            await message.answer(f"Не удалось прочитать APKG: {exc}")
            return
        await _import_notes(message, state, notes, source="apkg", media_files=media_files)
        return

    try:
        payload = decode_text_payload(buffer.getvalue())
    except UnicodeDecodeError:
        await message.answer("Не удалось прочитать кодировку файла. Поддерживаются UTF-8 и Windows-1251.")
        return
    await _import_text_payload(message, state, payload, source="import")


@router.message(ImportCards.payload)
async def import_text(message: Message, state: FSMContext) -> None:
    payload = message.text or ""
    await _import_text_payload(message, state, payload, source="import")


async def _import_text_payload(
    message: Message,
    state: FSMContext,
    payload: str,
    source: str,
) -> None:
    rows = [
        ImportedCard(front=front, back=back, tags=tags, create_reverse=create_reverse)
        for front, back, tags, create_reverse in parse_text_cards(payload)
    ]
    await _import_rows(message, state, rows, source=source, media_files=[])


async def _import_rows(
    message: Message,
    state: FSMContext,
    rows: list[ImportedCard],
    source: str,
    media_files,
) -> None:
    if message.from_user is None:
        return
    if not rows:
        await message.answer("Не удалось найти карточки. Нужны минимум две колонки: вопрос и ответ.")
        return

    data = await state.get_data()
    deck_cache = {}
    async with async_session() as session:
        user = await get_or_create_user(session, message.from_user)
        auto_decks = data["deck_id"] == "auto"
        deck = None
        if not auto_decks:
            deck = await get_deck(session, user, int(data["deck_id"]))
            if deck is None:
                await message.answer("Колода не найдена.")
                await state.clear()
                return

        imported = 0
        skipped = 0
        media_saved = 0
        media_skipped = 0
        media_saved_for_auto = False
        for row in rows:
            target_deck = deck
            if auto_decks:
                deck_name = row.deck_name or "Imported APKG"
                target_deck = deck_cache.get(deck_name)
                if target_deck is None:
                    target_deck = await get_or_create_deck(
                        session,
                        user,
                        deck_name,
                        "Imported from APKG",
                    )
                    deck_cache[deck_name] = target_deck
                    if auto_decks and media_files and not media_saved_for_auto:
                        saved, skipped_media = await save_imported_media_files(
                            session,
                            user,
                            None,
                            media_files,
                        )
                        media_saved += saved
                        media_skipped += skipped_media
                        media_saved_for_auto = True

            if target_deck is None:
                continue
            if await note_exists(session, user, target_deck, row.front, row.back):
                skipped += 1
                continue
            await create_basic_note(
                session=session,
                user=user,
                deck=target_deck,
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
            imported += 1

        if not auto_decks and deck is not None and media_files:
            saved, skipped_media = await save_imported_media_files(session, user, deck, media_files)
            media_saved += saved
            media_skipped += skipped_media
            await session.commit()

    await state.clear()
    await message.answer(
        f"Импортировано карточек: {imported}. Пропущено дублей: {skipped}.\n"
        f"Media saved: {media_saved}. Media skipped: {media_skipped}."
    )


async def _import_notes(
    message: Message,
    state: FSMContext,
    notes: list[ImportedNote],
    source: str,
    media_files,
) -> None:
    if message.from_user is None:
        return
    if not notes:
        await message.answer("Не удалось найти заметки в APKG.")
        return

    data = await state.get_data()
    deck_cache = {}
    async with async_session() as session:
        user = await get_or_create_user(session, message.from_user)
        auto_decks = data["deck_id"] == "auto"
        selected_deck = None
        if not auto_decks:
            selected_deck = await get_deck(session, user, int(data["deck_id"]))
            if selected_deck is None:
                await message.answer("Колода не найдена.")
                await state.clear()
                return

        added_notes = 0
        updated_notes = 0
        unchanged_notes = 0
        imported_cards = 0
        media_saved = 0
        media_skipped = 0
        media_saved_once = False

        for note in notes:
            note_deck = selected_deck
            if auto_decks:
                note_deck_name = note.deck_name or "Imported APKG"
                note_deck = await _get_cached_deck(session, user, deck_cache, note_deck_name)

            if note_deck is None:
                continue
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
                added_notes += 1
            elif result.status == "updated":
                updated_notes += 1
            else:
                unchanged_notes += 1
            imported_cards += result.added_cards

            if auto_decks and media_files and not media_saved_once:
                saved, skipped_media = await save_imported_media_files(session, user, None, media_files)
                media_saved += saved
                media_skipped += skipped_media
                media_saved_once = True
                await session.commit()

        if not auto_decks and selected_deck is not None and media_files:
            saved, skipped_media = await save_imported_media_files(
                session,
                user,
                selected_deck,
                media_files,
            )
            media_saved += saved
            media_skipped += skipped_media
            await session.commit()

    await state.clear()
    await message.answer(
        f"Добавлено: {added_notes}, обновлено: {updated_notes}, без изменений: {unchanged_notes}. "
        f"Новых карточек: {imported_cards}.\n"
        f"Media saved: {media_saved}. Media skipped: {media_skipped}."
    )


async def _get_cached_deck(session, user, cache, name: str):
    deck = cache.get(name)
    if deck is None:
        deck = await resolve_apkg_deck(session, user, name, "Imported from APKG")
        cache[name] = deck
    return deck
