from html import escape

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from bot.db import async_session
from bot.keyboards import back_to_menu, browse_quick_filters, browse_results
from bot.services.cards import card_answer, card_question, search_cards
from bot.services.leeches import is_leech
from bot.services.users import get_or_create_user
from bot.states import BrowseCards

router = Router()


@router.callback_query(F.data == "browse:start")
async def browse_start(callback: CallbackQuery, state: FSMContext) -> None:
    await callback.answer()
    if callback.message is None:
        return
    await state.set_state(BrowseCards.query)
    await callback.message.answer(
        "Введите текст или фильтр: tag:word, state:new, is:due, is:suspended, "
        "is:buried, is:leech, flag:red, deck:name.",
        reply_markup=browse_quick_filters(),
    )


@router.callback_query(F.data.startswith("browse:filter:"))
async def browse_quick_filter(callback: CallbackQuery, state: FSMContext) -> None:
    await callback.answer()
    if callback.from_user is None or callback.message is None or callback.data is None:
        return
    query = callback.data.removeprefix("browse:filter:")
    await _send_search_results(callback.message, callback.from_user, query)
    await state.clear()


@router.message(BrowseCards.query)
async def browse_query(message: Message, state: FSMContext) -> None:
    if message.from_user is None:
        return
    query = (message.text or "").strip()
    if not query:
        await message.answer("Поисковая строка не должна быть пустой.")
        return

    await state.clear()
    await _send_search_results(message, message.from_user, query)


async def _send_search_results(message: Message, tg_user, query: str) -> None:
    async with async_session() as session:
        user = await get_or_create_user(session, tg_user)
        cards = await search_cards(session, user, query)

    if not cards:
        await message.answer("Ничего не найдено.", reply_markup=back_to_menu())
        return

    lines = ["Найдено:"]
    buttons = []
    for card in cards:
        flags = []
        if card.suspended:
            flags.append("suspended")
        if card.buried_until:
            flags.append("buried")
        if card.flag:
            flags.append(f"flag:{card.flag}")
        if is_leech(card):
            flags.append(f"leech:{card.review_lapses}")
        status = f" ({', '.join(flags)})" if flags else ""
        lines.append(
            f"\n<b>#{card.id}</b> {escape(card.deck.name)} | {escape(card.state)}{escape(status)}\n"
            f"{escape(card_question(card))}\n{escape(card_answer(card))}"
        )
        buttons.append((card.id, f"#{card.id} {card_question(card)}"))
    await message.answer("\n".join(lines), reply_markup=browse_results(buttons))
