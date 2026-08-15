from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import BufferedInputFile, CallbackQuery, Message
from sqlalchemy.exc import IntegrityError

from bot.db import async_session
from bot.keyboards import archived_deck_list, deck_actions, deck_list
from bot.keyboards import yes_no
from bot.services.decks import (
    archive_deck,
    create_deck,
    deck_list_with_counts,
    deck_summary,
    get_any_deck,
    get_deck,
    list_archived_deck_display_choices,
    rename_deck,
    restore_deck,
)
from bot.services.exporters import export_deck_csv
from bot.services.stats import deck_review_stats
from bot.services.users import get_or_create_user
from bot.states import AddDeck, EditDeck

router = Router()


@router.callback_query(F.data == "decks:list")
async def list_decks(callback: CallbackQuery) -> None:
    await callback.answer()
    if callback.from_user is None or callback.message is None:
        return

    async with async_session() as session:
        user = await get_or_create_user(session, callback.from_user)
        rows = await deck_list_with_counts(session, user)

    if not rows:
        await callback.message.answer(
            "Пока нет колод. Создайте первую колоду.",
            reply_markup=deck_list([]),
        )
        return

    await callback.message.answer("Ваши колоды:", reply_markup=deck_list(rows))


@router.callback_query(F.data == "decks:archived")
async def list_archived(callback: CallbackQuery) -> None:
    await callback.answer()
    if callback.from_user is None or callback.message is None:
        return

    async with async_session() as session:
        user = await get_or_create_user(session, callback.from_user)
        rows = await list_archived_deck_display_choices(session, user)

    if not rows:
        await callback.message.answer("Архив пуст.", reply_markup=archived_deck_list([]))
        return
    await callback.message.answer(
        "Архивные колоды. Нажмите на колоду, чтобы восстановить её.",
        reply_markup=archived_deck_list(rows),
    )


@router.callback_query(F.data == "deck:add")
async def add_deck_start(callback: CallbackQuery, state: FSMContext) -> None:
    await callback.answer()
    if callback.message is None:
        return
    await state.set_state(AddDeck.name)
    await callback.message.answer("Введите название новой колоды.")


@router.message(AddDeck.name)
async def add_deck_name(message: Message, state: FSMContext) -> None:
    name = (message.text or "").strip()
    if not name:
        await message.answer("Название не должно быть пустым.")
        return
    await state.update_data(name=name)
    await state.set_state(AddDeck.description)
    await message.answer("Добавьте описание или напишите `-`, чтобы пропустить.")


@router.message(AddDeck.description)
async def add_deck_description(message: Message, state: FSMContext) -> None:
    if message.from_user is None:
        return
    data = await state.get_data()
    description = (message.text or "").strip()
    if description == "-":
        description = None

    async with async_session() as session:
        user = await get_or_create_user(session, message.from_user)
        try:
            deck = await create_deck(session, user, data["name"], description)
        except IntegrityError:
            await session.rollback()
            await message.answer("Колода с таким названием уже есть.")
            return

    await state.clear()
    await message.answer(
        f"Колода создана: {deck.name}",
        reply_markup=deck_actions(deck.id),
    )


@router.callback_query(F.data.startswith("deck:view:"))
async def view_deck(callback: CallbackQuery) -> None:
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
        summary = await deck_summary(session, deck)
        review_stats = await deck_review_stats(session, deck)

    text = (
        f"Колода: <b>{deck.name}</b>\n"
        f"Заметок: {summary['notes']}\n"
        f"Карточек: {summary['cards']}\n"
        f"Новые: {summary['new']}\n"
        f"В обучении: {summary['learning']}\n"
        f"К повторению: {summary['review']}\n"
        f"Повторений всего: {summary['reviews']}\n"
        f"Повторов за 7 дней: {review_stats['week_reviews']}\n"
        f"Retention за 7 дней: {review_stats['week_retention']}%"
    )
    await callback.message.answer(text, reply_markup=deck_actions(deck.id))


@router.callback_query(F.data.startswith("deck:export:"))
async def export_deck(callback: CallbackQuery) -> None:
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
        payload = await export_deck_csv(session, deck)

    filename = _safe_filename(deck.name) + ".csv"
    await callback.message.answer_document(
        BufferedInputFile(payload, filename=filename),
        caption=f"Экспорт колоды: {deck.name}",
    )


def _safe_filename(value: str) -> str:
    safe = "".join(ch if ch.isalnum() or ch in ("-", "_") else "_" for ch in value.strip())
    return safe[:80] or "deck"


@router.callback_query(F.data.startswith("deck:rename:"))
async def rename_deck_start(callback: CallbackQuery, state: FSMContext) -> None:
    await callback.answer()
    if callback.message is None or callback.data is None:
        return
    deck_id = int(callback.data.split(":")[-1])
    await state.update_data(deck_id=deck_id)
    await state.set_state(EditDeck.name)
    await callback.message.answer("Введите новое название колоды.")


@router.message(EditDeck.name)
async def rename_deck_finish(message: Message, state: FSMContext) -> None:
    if message.from_user is None:
        return
    name = (message.text or "").strip()
    if not name:
        await message.answer("Название не должно быть пустым.")
        return

    data = await state.get_data()
    async with async_session() as session:
        user = await get_or_create_user(session, message.from_user)
        deck = await get_deck(session, user, int(data["deck_id"]))
        if deck is None:
            await message.answer("Колода не найдена.")
            await state.clear()
            return
        try:
            await rename_deck(session, deck, name)
        except IntegrityError:
            await session.rollback()
            await message.answer("Колода с таким названием уже есть.")
            return

    await state.clear()
    await message.answer("Колода переименована.", reply_markup=deck_actions(int(data["deck_id"])))


@router.callback_query(F.data.startswith("deck:archive:"))
async def archive_deck_confirm(callback: CallbackQuery) -> None:
    await callback.answer()
    if callback.message is None or callback.data is None:
        return
    deck_id = int(callback.data.split(":")[-1])
    await callback.message.answer(
        "Архивировать колоду? Она исчезнет из рабочих списков, но данные останутся в базе.",
        reply_markup=yes_no(f"deck:archive_confirm:{deck_id}", f"deck:view:{deck_id}"),
    )


@router.callback_query(F.data.startswith("deck:archive_confirm:"))
async def archive_deck_finish(callback: CallbackQuery) -> None:
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
        await archive_deck(session, deck)

    await callback.message.answer("Колода архивирована.")


@router.callback_query(F.data.startswith("deck:restore:"))
async def restore_deck_handler(callback: CallbackQuery) -> None:
    await callback.answer()
    if callback.from_user is None or callback.message is None or callback.data is None:
        return
    deck_id = int(callback.data.split(":")[-1])

    async with async_session() as session:
        user = await get_or_create_user(session, callback.from_user)
        deck = await get_any_deck(session, user, deck_id)
        if deck is None:
            await callback.message.answer("Колода не найдена.")
            return
        await restore_deck(session, deck)

    await callback.message.answer("Колода восстановлена.", reply_markup=deck_actions(deck_id))
