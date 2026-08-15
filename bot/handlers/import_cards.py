from io import BytesIO

from aiogram import F, Bot, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from bot.db import async_session
from bot.keyboards import choose_import_deck
from bot.services.apkg_importer import ImportedCard, ImportedNote, parse_apkg_media, parse_apkg_notes
from bot.services.decks import list_user_deck_display_choices, resolve_apkg_deck
from bot.services.importers import decode_text_payload, parse_text_cards
from bot.services.import_flow import ImportFlowError, import_apkg_notes, import_text_cards
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

    data = await state.get_data()
    async with async_session() as session:
        user = await get_or_create_user(session, message.from_user)
        try:
            result = await import_text_cards(session, user, int(data["deck_id"]), rows, source)
        except (ImportFlowError, LookupError) as exc:
            await message.answer(str(exc))
            return

    await state.clear()
    await message.answer(
        f"Импортировано карточек: {result.added}. Пропущено дублей: {result.unchanged}.\n"
        f"Media saved: {result.media_saved}. Media skipped: {result.media_skipped}."
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

    data = await state.get_data()
    async with async_session() as session:
        user = await get_or_create_user(session, message.from_user)
        try:
            result = await import_apkg_notes(
                session,
                user,
                None if data["deck_id"] == "auto" else int(data["deck_id"]),
                notes,
                media_files,
                source,
            )
        except (ImportFlowError, LookupError) as exc:
            await message.answer(str(exc))
            return

    await state.clear()
    await message.answer(
        f"Добавлено: {result.added}, обновлено: {result.updated}, без изменений: {result.unchanged}. "
        f"Новых карточек: {result.added_cards}.\n"
        f"Media saved: {result.media_saved}. Media skipped: {result.media_skipped}."
    )


async def _get_cached_deck(session, user, cache, name: str):
    deck = cache.get(name)
    if deck is None:
        deck = await resolve_apkg_deck(session, user, name, "Imported from APKG")
        cache[name] = deck
    return deck
