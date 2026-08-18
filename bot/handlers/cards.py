from datetime import UTC, datetime
from html import escape

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from bot.db import async_session
from bot.keyboards import (
    card_actions,
    choose_deck,
    flag_options,
    note_edit_fields,
    set_due_options,
    yes_no,
)
from bot.services.cards import (
    bury_card_until_tomorrow,
    card_answer,
    card_question,
    create_basic_note,
    delete_note,
    get_card,
    get_note,
    reset_card,
    set_card_due_in_days,
    set_card_flag,
    set_card_suspended,
    update_note_field,
)
from bot.services.decks import get_deck, list_user_deck_display_choices
from bot.services.events import track
from bot.services.users import get_or_create_user
from bot.states import AddCard, EditNote, SetDueDate

router = Router()


@router.callback_query(F.data == "card:add")
async def add_card_choose_deck(callback: CallbackQuery, state: FSMContext) -> None:
    await callback.answer()
    if callback.from_user is None or callback.message is None:
        return

    async with async_session() as session:
        user = await get_or_create_user(session, callback.from_user)
        deck_choices = await list_user_deck_display_choices(session, user)

    if not deck_choices:
        await callback.message.answer("Сначала создайте колоду.")
        return

    await state.set_state(AddCard.deck_id)
    await callback.message.answer(
        "Выберите колоду для новой карточки.",
        reply_markup=choose_deck("card:add", deck_choices),
    )


@router.callback_query(F.data.startswith("card:add:"))
async def add_card_start(callback: CallbackQuery, state: FSMContext) -> None:
    await callback.answer()
    if callback.message is None or callback.data is None:
        return
    deck_id = int(callback.data.split(":")[-1])
    await state.update_data(deck_id=deck_id)
    await state.set_state(AddCard.front)
    await callback.message.answer("Введите лицевую сторону карточки.")


@router.message(AddCard.front)
async def add_card_front(message: Message, state: FSMContext) -> None:
    front = (message.text or "").strip()
    if not front:
        await message.answer("Лицевая сторона не должна быть пустой.")
        return
    await state.update_data(front=front)
    await state.set_state(AddCard.back)
    await message.answer("Введите обратную сторону карточки.")


@router.message(AddCard.back)
async def add_card_back(message: Message, state: FSMContext) -> None:
    back = (message.text or "").strip()
    if not back:
        await message.answer("Обратная сторона не должна быть пустой.")
        return
    await state.update_data(back=back)
    await state.set_state(AddCard.tags)
    await message.answer("Введите теги через пробел или `-`, чтобы пропустить.")


@router.message(AddCard.tags)
async def add_card_tags(message: Message, state: FSMContext) -> None:
    raw_tags = (message.text or "").strip()
    tags = [] if raw_tags == "-" else [tag for tag in raw_tags.split() if tag]
    await state.update_data(tags=tags)
    await state.set_state(AddCard.reverse)
    await message.answer(
        "Создать обратную карточку тоже?",
        reply_markup=yes_no("card:reverse:yes", "card:reverse:no"),
    )


@router.callback_query(F.data.in_(["card:reverse:yes", "card:reverse:no"]))
async def add_card_finish(callback: CallbackQuery, state: FSMContext) -> None:
    await callback.answer()
    if callback.from_user is None or callback.message is None or callback.data is None:
        return

    data = await state.get_data()
    create_reverse = callback.data.endswith(":yes")
    async with async_session() as session:
        user = await get_or_create_user(session, callback.from_user)
        deck = await get_deck(session, user, int(data["deck_id"]))
        if deck is None:
            await callback.message.answer("Колода не найдена.")
            await state.clear()
            return
        await create_basic_note(
            session=session,
            user=user,
            deck=deck,
            front=data["front"],
            back=data["back"],
            tags=data.get("tags") or [],
            create_reverse=create_reverse,
            source="manual",
            commit=False,
        )
        await track(session, user.id, "card_created", reverse=create_reverse)
        await session.commit()

    await state.clear()
    await callback.message.answer("Карточка добавлена.")


@router.callback_query(F.data.startswith("card:view:"))
async def view_card(callback: CallbackQuery) -> None:
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

        text = _card_details(card)
        await callback.message.answer(
            text,
            reply_markup=card_actions(
                card_id=card.id,
                note_id=card.note_id,
                deck_id=card.deck_id,
                suspended=card.suspended,
                flag=card.flag,
            ),
        )


@router.callback_query(F.data.startswith("note:edit:"))
async def choose_note_field(callback: CallbackQuery) -> None:
    await callback.answer()
    if callback.message is None or callback.data is None:
        return
    _, _, note_id_raw, card_id_raw = callback.data.split(":")
    await callback.message.answer(
        "Что изменить?",
        reply_markup=note_edit_fields(int(note_id_raw), int(card_id_raw)),
    )


@router.callback_query(F.data.startswith("note:edit_field:"))
async def edit_note_field_start(callback: CallbackQuery, state: FSMContext) -> None:
    await callback.answer()
    if callback.message is None or callback.data is None:
        return
    _, _, note_id_raw, card_id_raw, field = callback.data.split(":")
    await state.update_data(note_id=int(note_id_raw), card_id=int(card_id_raw), field=field)
    await state.set_state(EditNote.value)

    labels = {
        "front": "новый вопрос",
        "back": "новый ответ",
        "tags": "теги через пробел",
    }
    await callback.message.answer(f"Введите {labels[field]}.")


@router.message(EditNote.value)
async def edit_note_field_finish(message: Message, state: FSMContext) -> None:
    if message.from_user is None:
        return
    value = (message.text or "").strip()
    if not value:
        await message.answer("Значение не должно быть пустым.")
        return

    data = await state.get_data()
    async with async_session() as session:
        user = await get_or_create_user(session, message.from_user)
        note = await get_note(session, user, int(data["note_id"]))
        if note is None:
            await message.answer("Заметка не найдена.")
            await state.clear()
            return
        await update_note_field(session, note, data["field"], value)

    card_id = int(data["card_id"])
    await state.clear()
    await message.answer("Изменения сохранены. Вернуться к карточке?", reply_markup=yes_no(f"card:view:{card_id}", "menu:main"))


@router.callback_query(F.data.startswith("note:delete:"))
async def delete_note_confirm(callback: CallbackQuery) -> None:
    await callback.answer()
    if callback.message is None or callback.data is None:
        return
    _, _, note_id_raw, card_id_raw = callback.data.split(":")
    await callback.message.answer(
        "Удалить заметку и все её карточки?",
        reply_markup=yes_no(
            f"note:delete_confirm:{note_id_raw}:{card_id_raw}",
            f"card:view:{card_id_raw}",
        ),
    )


@router.callback_query(F.data.startswith("note:delete_confirm:"))
async def delete_note_finish(callback: CallbackQuery) -> None:
    await callback.answer()
    if callback.from_user is None or callback.message is None or callback.data is None:
        return
    _, _, note_id_raw, _card_id_raw = callback.data.split(":")

    async with async_session() as session:
        user = await get_or_create_user(session, callback.from_user)
        note = await get_note(session, user, int(note_id_raw))
        if note is None:
            await callback.message.answer("Заметка не найдена.")
            return
        await delete_note(session, note)

    await callback.message.answer("Заметка удалена.")


@router.callback_query(F.data.startswith("card:suspend:"))
async def suspend_card(callback: CallbackQuery) -> None:
    await callback.answer()
    if callback.from_user is None or callback.message is None or callback.data is None:
        return
    _, _, card_id_raw, suspended_raw = callback.data.split(":")

    async with async_session() as session:
        user = await get_or_create_user(session, callback.from_user)
        card = await get_card(session, user, int(card_id_raw))
        if card is None:
            await callback.message.answer("Карточка не найдена.")
            return
        await set_card_suspended(session, card, suspended_raw == "1")

    await callback.message.answer("Состояние карточки обновлено.", reply_markup=yes_no(f"card:view:{card_id_raw}", "menu:main"))


@router.callback_query(F.data.startswith("card:bury:"))
async def bury_card(callback: CallbackQuery) -> None:
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
        await bury_card_until_tomorrow(session, card, user.timezone)

    await callback.message.answer("Карточка скрыта до завтра.")


@router.callback_query(F.data.startswith("card:due:"))
async def choose_due(callback: CallbackQuery) -> None:
    await callback.answer()
    if callback.message is None or callback.data is None:
        return
    card_id = int(callback.data.split(":")[-1])
    await callback.message.answer("Когда показать карточку снова?", reply_markup=set_due_options(card_id))


@router.callback_query(F.data.startswith("card:due_set:"))
async def set_due_from_button(callback: CallbackQuery) -> None:
    await callback.answer()
    if callback.from_user is None or callback.message is None or callback.data is None:
        return
    _, _, card_id_raw, days_raw = callback.data.split(":")

    async with async_session() as session:
        user = await get_or_create_user(session, callback.from_user)
        card = await get_card(session, user, int(card_id_raw))
        if card is None:
            await callback.message.answer("Карточка не найдена.")
            return
        await set_card_due_in_days(session, card, int(days_raw))

    await callback.message.answer("Дата повтора обновлена.", reply_markup=yes_no(f"card:view:{card_id_raw}", "menu:main"))


@router.callback_query(F.data.startswith("card:due_custom:"))
async def set_due_custom_start(callback: CallbackQuery, state: FSMContext) -> None:
    await callback.answer()
    if callback.message is None or callback.data is None:
        return
    card_id = int(callback.data.split(":")[-1])
    await state.update_data(card_id=card_id)
    await state.set_state(SetDueDate.value)
    await callback.message.answer("Введите количество дней или дату в формате YYYY-MM-DD.")


@router.message(SetDueDate.value)
async def set_due_custom_finish(message: Message, state: FSMContext) -> None:
    if message.from_user is None:
        return
    raw = (message.text or "").strip()
    days = _parse_due_days(raw)
    if days is None:
        await message.answer("Не понял дату. Пример: `7` или `2026-06-10`.")
        return

    data = await state.get_data()
    card_id = int(data["card_id"])
    async with async_session() as session:
        user = await get_or_create_user(session, message.from_user)
        card = await get_card(session, user, card_id)
        if card is None:
            await message.answer("Карточка не найдена.")
            await state.clear()
            return
        await set_card_due_in_days(session, card, days)

    await state.clear()
    await message.answer("Дата повтора обновлена.", reply_markup=yes_no(f"card:view:{card_id}", "menu:main"))


@router.callback_query(F.data.startswith("card:reset:"))
async def reset_card_handler(callback: CallbackQuery) -> None:
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
        await reset_card(session, card)

    await callback.message.answer("Карточка сброшена в новые.", reply_markup=yes_no(f"card:view:{card_id}", "menu:main"))


@router.callback_query(F.data.startswith("card:flag:"))
async def flag_card_handler(callback: CallbackQuery) -> None:
    await callback.answer()
    if callback.from_user is None or callback.message is None or callback.data is None:
        return
    _, _, card_id_raw, flag_raw = callback.data.split(":")
    flag = None if flag_raw == "none" else flag_raw

    async with async_session() as session:
        user = await get_or_create_user(session, callback.from_user)
        card = await get_card(session, user, int(card_id_raw))
        if card is None:
            await callback.message.answer("Карточка не найдена.")
            return
        await set_card_flag(session, card, flag)

    await callback.message.answer("Флаг обновлён.", reply_markup=yes_no(f"card:view:{card_id_raw}", "menu:main"))


@router.callback_query(F.data.startswith("card:flag_menu:"))
async def flag_card_menu(callback: CallbackQuery) -> None:
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
        await callback.message.answer("Выберите флаг.", reply_markup=flag_options(card.id, card.flag))


def _card_details(card) -> str:
    due_at = card.due_at.astimezone(UTC).strftime("%Y-%m-%d %H:%M UTC")
    tags = ", ".join(card.note.tags or []) or "нет"
    flags = []
    if card.suspended:
        flags.append("suspended")
    if card.buried_until:
        flags.append(f"buried until {card.buried_until.isoformat()}")
    if card.flag:
        flags.append(f"flag: {card.flag}")
    status = ", ".join(flags) if flags else "активна"
    return (
        f"<b>Карточка #{card.id}</b>\n"
        f"Колода: {escape(card.deck.name)}\n"
        f"Состояние: {escape(card.state)}\n"
        f"Статус: {escape(status)}\n"
        f"Повторов: {card.reps}, ошибок: {card.lapses}\n"
        f"Следующий показ: {due_at}\n"
        f"Теги: {escape(tags)}\n\n"
        f"<b>Вопрос</b>\n{escape(card_question(card))}\n\n"
        f"<b>Ответ</b>\n{escape(card_answer(card))}"
    )


def _parse_due_days(raw: str) -> int | None:
    if raw.isdigit():
        return int(raw)
    try:
        target = datetime.strptime(raw, "%Y-%m-%d").replace(tzinfo=UTC)
    except ValueError:
        return None
    today = datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
    return max((target - today).days, 0)
