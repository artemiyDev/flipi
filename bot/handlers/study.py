from datetime import UTC, datetime
from html import escape

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import BufferedInputFile, CallbackQuery, Message

from bot.db import async_session
from bot.keyboards import choose_study_deck as choose_study_deck_keyboard
from bot.keyboards import leech_rescue, rate_card, show_answer
from bot.services.cards import (
    card_answer,
    card_question,
    count_cards_by_query,
    count_due_cards_by_query,
    get_card,
    get_next_due_card,
    get_next_due_card_by_query,
    get_next_new_card_without_limit,
    get_next_review_ahead_card,
    learn_ahead_payload,
)
from bot.services.decks import get_deck, list_user_deck_display_choices
from bot.services.leeches import LeechResumeConflictError, defer_leech, resume_leech
from bot.services.media import extract_media_references, get_media_files_by_names, strip_media_references
from bot.services.study import (
    AnswerRequestConflictError,
    SuspendedCardAnswerError,
    answer_card_request,
    get_next_card_for_user,
)
from bot.services.users import get_or_create_user
from bot.states import FilteredStudy

router = Router()


@router.callback_query(F.data == "study:choose")
async def choose_study_deck_handler(callback: CallbackQuery) -> None:
    await callback.answer()
    if callback.from_user is None or callback.message is None:
        return

    async with async_session() as session:
        user = await get_or_create_user(session, callback.from_user)
        deck_choices = await list_user_deck_display_choices(session, user)

    if not deck_choices:
        await callback.message.answer("Сначала создайте колоду и добавьте карточки.")
        return

    await callback.message.answer(
        "Выберите колоду для занятия.",
        reply_markup=choose_study_deck_keyboard(deck_choices),
    )


@router.callback_query(F.data == "study:filter")
async def filtered_study_start(callback: CallbackQuery, state: FSMContext) -> None:
    await callback.answer()
    if callback.message is None:
        return
    await state.set_state(FilteredStudy.query)
    await callback.message.answer(
        "Введите фильтр для временной учебной сессии: tag:word, deck:name, state:new, is:due или обычный текст."
    )


@router.message(FilteredStudy.query)
async def filtered_study_query(message: Message, state: FSMContext) -> None:
    if message.from_user is None:
        return
    query = (message.text or "").strip()
    if not query:
        await message.answer("Фильтр не должен быть пустым.")
        return

    async with async_session() as session:
        user = await get_or_create_user(session, message.from_user)
        now_utc = datetime.now(UTC)
        total_count = await count_cards_by_query(session, user, query, now_utc)
        due_count = await count_due_cards_by_query(session, user, query, now_utc)
        card = await get_next_due_card_by_query(session, user, query, now_utc)
        await state.update_data(study_scope="filter", filter_query=query)
        await state.set_state(None)
        if card is None:
            await message.answer(
                f"Фильтр: {query}\nНайдено карточек: {total_count}\nДоступно сейчас: {due_count}\n"
                "По этому фильтру сейчас нет карточек для занятия."
            )
            return
        await message.answer(
            f"Фильтр: {query}\nНайдено карточек: {total_count}\nДоступно сейчас: {due_count}"
        )
        await _send_card_question(message, session, user, card, now_utc)


@router.callback_query(F.data.startswith("study:start:"))
async def start_study(callback: CallbackQuery, state: FSMContext) -> None:
    await callback.answer()
    if callback.from_user is None or callback.message is None or callback.data is None:
        return
    scope = callback.data.split(":")[-1]

    async with async_session() as session:
        user = await get_or_create_user(session, callback.from_user)
        now_utc = datetime.now(UTC)
        if scope == "all":
            await state.update_data(study_scope="all")
            card = await get_next_card_for_user(session, user, now_utc)
        else:
            deck_id = int(scope)
            await state.update_data(study_scope="deck", deck_id=deck_id)
            deck = await get_deck(session, user, deck_id)
            if deck is None:
                await callback.message.answer("Колода не найдена.")
                return
            card = await get_next_due_card(session, deck, user.timezone, now_utc)
        if card is None:
            await callback.message.answer("На сейчас карточек нет. Можно добавить новые или вернуться позже.")
            return
        await _send_card_question(callback.message, session, user, card, now_utc)


@router.callback_query(F.data.startswith("study:ahead:"))
async def start_review_ahead(callback: CallbackQuery, state: FSMContext) -> None:
    await callback.answer()
    if callback.from_user is None or callback.message is None or callback.data is None:
        return
    deck_id = int(callback.data.split(":")[-1])

    async with async_session() as session:
        user = await get_or_create_user(session, callback.from_user)
        deck = await get_deck(session, user, deck_id)
        if deck is None:
            await callback.message.answer("Колода не найдена.")
            return
        card = await get_next_review_ahead_card(session, deck, user.timezone)
        await state.update_data(study_scope="review_ahead", deck_id=deck_id)
        if card is None:
            await callback.message.answer("В этой колоде нет будущих review-карточек.")
            return
        await callback.message.answer("Custom study: повторение заранее.")
        await _send_card_question(callback.message, session, user, card)


@router.callback_query(F.data.startswith("study:new:"))
async def start_new_without_limit(callback: CallbackQuery, state: FSMContext) -> None:
    await callback.answer()
    if callback.from_user is None or callback.message is None or callback.data is None:
        return
    deck_id = int(callback.data.split(":")[-1])

    async with async_session() as session:
        user = await get_or_create_user(session, callback.from_user)
        deck = await get_deck(session, user, deck_id)
        if deck is None:
            await callback.message.answer("Колода не найдена.")
            return
        card = await get_next_new_card_without_limit(session, deck, user.timezone)
        await state.update_data(study_scope="new_without_limit", deck_id=deck_id)
        if card is None:
            await callback.message.answer("В этой колоде нет новых карточек.")
            return
        await callback.message.answer("Custom study: новые карточки без дневного лимита.")
        await _send_card_question(callback.message, session, user, card)


@router.callback_query(F.data.startswith("study:show:"))
async def reveal_answer(callback: CallbackQuery) -> None:
    await callback.answer()
    if callback.from_user is None or callback.message is None or callback.data is None:
        return
    card_id = int(callback.data.split(":")[-1])

    async with async_session() as session:
        user = await get_or_create_user(session, callback.from_user)
        card = await get_card(session, user, card_id)
        if card is None:
            await callback.message.answer("Карточка не найдена.")
            return
        await _send_card_answer(callback.message, session, user, card)


@router.callback_query(F.data.startswith("study:rate:"))
async def rate_study_card(callback: CallbackQuery, state: FSMContext) -> None:
    await callback.answer()
    if callback.from_user is None or callback.message is None or callback.data is None:
        return
    _, _, card_id_raw, rating_raw = callback.data.split(":")
    card_id = int(card_id_raw)
    rating = int(rating_raw)
    request_id = f"tg:{callback.message.chat.id}:{callback.message.message_id}"

    async with async_session() as session:
        user = await get_or_create_user(session, callback.from_user)
        try:
            result = await answer_card_request(
                session,
                user,
                card_id,
                rating,
                request_id=request_id,
            )
        except AnswerRequestConflictError:
            await callback.message.answer("Ответ уже сохранён с другой оценкой.")
            return
        except SuspendedCardAnswerError:
            await callback.message.answer("Карточка уже приостановлена.")
            return
        if result is None:
            await callback.message.answer("Карточка не найдена.")
            return
        card = await get_card(session, user, card_id)
        if card is None:
            await callback.message.answer("Карточка не найдена.")
            return
        if result.leech_alert_lapses is not None:
            await callback.message.answer(
                _leech_text(result.leech_alert_lapses),
                reply_markup=leech_rescue(
                    card.id,
                    card.note_id,
                    result.leech_alert_lapses,
                ),
            )
            return
        if result.replayed:
            return

        now_utc = datetime.now(UTC)
        next_card = await _next_card_for_scope(session, user, card, state, now_utc)
        if next_card is None:
            await callback.message.answer("Готово. На сейчас карточек нет.")
            return

        await _send_card_question(callback.message, session, user, next_card, now_utc)


@router.callback_query(F.data.startswith("leech:resume:"))
async def resume_leech_study(callback: CallbackQuery, state: FSMContext) -> None:
    await callback.answer()
    if callback.from_user is None or callback.message is None or callback.data is None:
        return
    _, _, card_id_raw, review_lapses_raw = callback.data.split(":")
    card_id = int(card_id_raw)
    expected_review_lapses = int(review_lapses_raw)

    async with async_session() as session:
        user = await get_or_create_user(session, callback.from_user)
        try:
            result = await resume_leech(
                session,
                user,
                card_id,
                expected_review_lapses,
            )
        except LeechResumeConflictError:
            await callback.message.answer(
                "Состояние карточки изменилось. Откройте её и проверьте статус."
            )
            return
        if result is None:
            await callback.message.answer("Карточка не найдена.")
            return
        if result.replayed:
            return

        card = await get_card(session, user, card_id)
        if card is None:
            await callback.message.answer("Карточка не найдена.")
            return
        now_utc = datetime.now(UTC)
        next_card = await _next_card_for_scope(session, user, card, state, now_utc)
        if next_card is None:
            await callback.message.answer("Готово. На сейчас карточек нет.")
            return
        await _send_card_question(callback.message, session, user, next_card, now_utc)


@router.callback_query(F.data.startswith("leech:later:"))
async def leave_leech_for_later(callback: CallbackQuery, state: FSMContext) -> None:
    await callback.answer()
    if callback.from_user is None or callback.message is None or callback.data is None:
        return
    _, _, card_id_raw, review_lapses_raw = callback.data.split(":")
    card_id = int(card_id_raw)
    expected_review_lapses = int(review_lapses_raw)

    async with async_session() as session:
        user = await get_or_create_user(session, callback.from_user)
        try:
            result = await defer_leech(
                session,
                user,
                card_id,
                expected_review_lapses,
            )
        except LeechResumeConflictError:
            await callback.message.answer(
                "Состояние карточки изменилось. Откройте её и проверьте статус."
            )
            return
        if result is None:
            await callback.message.answer("Карточка не найдена.")
            return
        if result.replayed:
            return
        card = await get_card(session, user, card_id)
        if card is None:
            await callback.message.answer("Карточка не найдена.")
            return
        now_utc = datetime.now(UTC)
        next_card = await _next_card_for_scope(session, user, card, state, now_utc)
        if next_card is None:
            await callback.message.answer("Готово. На сейчас карточек нет.")
            return
        await _send_card_question(callback.message, session, user, next_card, now_utc)


async def _next_card_for_scope(
    session,
    user,
    card,
    state: FSMContext,
    now_utc: datetime,
):
    data = await state.get_data()
    if data.get("study_scope") == "all":
        return await get_next_card_for_user(session, user, now_utc)
    if data.get("study_scope") == "filter":
        return await get_next_due_card_by_query(
            session,
            user,
            data.get("filter_query", ""),
            now_utc,
        )
    if data.get("study_scope") == "review_ahead":
        return await get_next_review_ahead_card(session, card.deck, user.timezone)
    if data.get("study_scope") == "new_without_limit":
        return await get_next_new_card_without_limit(session, card.deck, user.timezone)
    return await get_next_due_card(session, card.deck, user.timezone, now_utc)


def _leech_text(review_lapses: int) -> str:
    return (
        f"<b>Карточка забыта {review_lapses} раза</b>\n\n"
        "Мы приостановили её, чтобы она не забирала время. "
        "Упростите вопрос, разбейте материал или добавьте подсказку."
    )


def _question_text(deck_name: str, card) -> str:
    question = strip_media_references(card_question(card)) or "[media]"
    return f"<b>{escape(deck_name)}</b>\n\n<b>Вопрос</b>\n{escape(question)}"


def _answer_text(card) -> str:
    question = strip_media_references(card_question(card)) or "[media]"
    answer = strip_media_references(card_answer(card)) or "[media]"
    return (
        f"<b>Вопрос</b>\n{escape(question)}\n\n"
        f"<b>Ответ</b>\n{escape(answer)}"
    )


async def _send_card_question(
    message: Message,
    session,
    user,
    card,
    now_utc: datetime | None = None,
) -> None:
    await _send_media_for_texts(message, session, user, card_question(card))
    question = _question_text(card.deck.name, card)
    hint = _learn_ahead_hint(card, now_utc or datetime.now(UTC))
    if hint is not None:
        question = f"{hint}\n\n{question}"
    await message.answer(question, reply_markup=show_answer(card.id))


def _learn_ahead_hint(card, now_utc: datetime) -> str | None:
    payload = learn_ahead_payload(card, now_utc)
    if payload is None:
        return None
    minutes = max(1, (int(payload["seconds_early"]) + 59) // 60)
    return f"Повтор чуть раньше · через {minutes} мин"


async def _send_card_answer(message: Message, session, user, card) -> None:
    await _send_media_for_texts(message, session, user, card_answer(card))
    await message.answer(_answer_text(card), reply_markup=rate_card(card.id))


async def _send_media_for_texts(message: Message, session, user, *texts: str) -> None:
    names = extract_media_references(*texts)
    media_files = await get_media_files_by_names(session, user, names)
    for media in media_files:
        file = BufferedInputFile(media.content, filename=media.original_name)
        content_type = media.content_type or ""
        if content_type.startswith("image/"):
            await message.answer_photo(file)
        elif content_type.startswith("audio/"):
            await message.answer_audio(file)
        else:
            await message.answer_document(file)
