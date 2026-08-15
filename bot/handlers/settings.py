from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery
from aiogram.types import Message
from zoneinfo import ZoneInfoNotFoundError

from bot.db import async_session
from bot.keyboards import (
    back_to_menu,
    choose_deck,
    deck_preset_options,
    deck_settings,
    settings_root,
)
from bot.services.decks import (
    DECK_OPTION_PRESETS,
    apply_deck_preset,
    get_deck,
    list_user_deck_display_choices,
    list_user_decks,
    toggle_bury_siblings,
    toggle_fuzzing,
    validate_deck_setting_value,
    update_deck_setting,
)
from bot.services.stats import daily_review_counts, user_stats
from bot.services.users import get_or_create_user, update_user_timezone
from bot.states import EditDeckSetting, EditUserTimezone

router = Router()


@router.callback_query(F.data == "settings:menu")
async def settings_menu(callback: CallbackQuery) -> None:
    await callback.answer()
    if callback.from_user is None or callback.message is None:
        return
    async with async_session() as session:
        user = await get_or_create_user(session, callback.from_user)
        decks = await list_user_decks(session, user)
    await callback.message.answer(
        "Настройки.",
        reply_markup=settings_root(user.timezone, bool(decks)),
    )


@router.callback_query(F.data == "settings:decks")
async def settings_decks(callback: CallbackQuery) -> None:
    await callback.answer()
    if callback.from_user is None or callback.message is None:
        return
    async with async_session() as session:
        user = await get_or_create_user(session, callback.from_user)
        deck_choices = await list_user_deck_display_choices(session, user)
    if not deck_choices:
        await callback.message.answer("Сначала создайте колоду.", reply_markup=back_to_menu())
        return
    await callback.message.answer(
        "Выберите колоду для настройки.",
        reply_markup=choose_deck("settings:deck", deck_choices),
    )


@router.callback_query(F.data == "settings:timezone")
async def edit_timezone_start(callback: CallbackQuery, state: FSMContext) -> None:
    await callback.answer()
    if callback.message is None:
        return
    await state.set_state(EditUserTimezone.value)
    await callback.message.answer("Введите timezone из базы IANA, например UTC, Europe/Moscow или America/Sao_Paulo.")


@router.message(EditUserTimezone.value)
async def edit_timezone_finish(message: Message, state: FSMContext) -> None:
    if message.from_user is None:
        return
    timezone_name = (message.text or "").strip()
    async with async_session() as session:
        user = await get_or_create_user(session, message.from_user)
        try:
            await update_user_timezone(session, user, timezone_name)
        except ZoneInfoNotFoundError:
            await message.answer("Не нашёл такой timezone. Пример корректного значения: Europe/Moscow.")
            return
        decks = await list_user_decks(session, user)

    await state.clear()
    await message.answer(
        f"Timezone сохранён: {timezone_name}",
        reply_markup=settings_root(timezone_name, bool(decks)),
    )


@router.callback_query(F.data.startswith("settings:deck:"))
async def deck_settings_menu(callback: CallbackQuery) -> None:
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

    text = (
        f"<b>{deck.name}</b>\n"
        f"Новых карточек в день: {deck.new_cards_per_day}\n"
        f"Повторов в день: {deck.reviews_per_day}\n"
        f"Desired retention: {deck.desired_retention:.2f}\n"
        f"Preset: {deck.option_preset}\n"
        f"Learning steps: {_format_steps(deck.learning_steps_minutes)} min\n"
        f"Relearning steps: {_format_steps(deck.relearning_steps_minutes)} min\n"
        f"Max interval: {deck.maximum_interval_days} days\n"
        f"Fuzzing: {'on' if deck.enable_fuzzing else 'off'}\n"
        f"Bury siblings: {'on' if deck.bury_siblings else 'off'}"
    )
    await callback.message.answer(text, reply_markup=deck_settings(deck.id))


@router.callback_query(F.data.startswith("settings:presets:"))
async def deck_presets_menu(callback: CallbackQuery) -> None:
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
    await callback.message.answer(
        "Выберите preset настроек колоды.",
        reply_markup=deck_preset_options(deck_id, deck.option_preset),
    )


@router.callback_query(F.data.startswith("settings:preset:"))
async def apply_preset(callback: CallbackQuery) -> None:
    await callback.answer()
    if callback.from_user is None or callback.message is None or callback.data is None:
        return
    _, _, deck_id_raw, preset_name = callback.data.split(":")
    if preset_name not in DECK_OPTION_PRESETS:
        await callback.message.answer("Неизвестный preset.")
        return
    deck_id = int(deck_id_raw)
    async with async_session() as session:
        user = await get_or_create_user(session, callback.from_user)
        deck = await get_deck(session, user, deck_id)
        if deck is None:
            await callback.message.answer("Колода не найдена.")
            return
        await apply_deck_preset(session, deck, preset_name)
    await callback.message.answer(
        f"Preset применён: {preset_name}",
        reply_markup=deck_settings(deck_id),
    )


@router.callback_query(F.data.startswith("settings:toggle_bury:"))
async def toggle_bury_setting(callback: CallbackQuery) -> None:
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
        await toggle_bury_siblings(session, deck)
        value = deck.bury_siblings

    await callback.message.answer(
        f"Bury siblings: {'on' if value else 'off'}",
        reply_markup=deck_settings(deck_id),
    )


@router.callback_query(F.data.startswith("settings:toggle_fuzz:"))
async def toggle_fuzz_setting(callback: CallbackQuery) -> None:
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
        await toggle_fuzzing(session, deck)
        value = deck.enable_fuzzing

    await callback.message.answer(
        f"Fuzzing: {'on' if value else 'off'}",
        reply_markup=deck_settings(deck_id),
    )


@router.callback_query(F.data.startswith("settings:edit:"))
async def edit_deck_setting_start(callback: CallbackQuery, state: FSMContext) -> None:
    await callback.answer()
    if callback.message is None or callback.data is None:
        return
    _, _, deck_id_raw, field = callback.data.split(":")
    await state.update_data(deck_id=int(deck_id_raw), field=field)
    await state.set_state(EditDeckSetting.value)
    labels = {
        "new_cards_per_day": "лимит новых карточек в день, например 20",
        "reviews_per_day": "лимит повторений в день, например 200",
        "desired_retention": "retention от 0.70 до 0.97, например 0.90",
        "learning_steps_minutes": "learning steps в минутах через запятую, например 1,10",
        "relearning_steps_minutes": "relearning steps в минутах через запятую, например 10",
        "maximum_interval_days": "максимальный интервал в днях, например 36500",
    }
    await callback.message.answer(f"Введите {labels[field]}.")


@router.message(EditDeckSetting.value)
async def edit_deck_setting_finish(message: Message, state: FSMContext) -> None:
    if message.from_user is None:
        return
    data = await state.get_data()
    field = data["field"]
    parsed = validate_deck_setting_value(field, message.text or "")
    if parsed is None:
        await message.answer("Значение вне допустимого диапазона.")
        return

    async with async_session() as session:
        user = await get_or_create_user(session, message.from_user)
        deck = await get_deck(session, user, int(data["deck_id"]))
        if deck is None:
            await message.answer("Колода не найдена.")
            await state.clear()
            return
        await update_deck_setting(session, deck, field, parsed)

    await state.clear()
    await message.answer("Настройка сохранена.", reply_markup=deck_settings(int(data["deck_id"])))


@router.callback_query(F.data == "stats:summary")
async def stats_summary(callback: CallbackQuery) -> None:
    await callback.answer()
    if callback.from_user is None or callback.message is None:
        return
    async with async_session() as session:
        user = await get_or_create_user(session, callback.from_user)
        stats = await user_stats(session, user)
        daily_counts = await daily_review_counts(session, user)

    history = "\n".join(_format_day_count(day, count) for day, count in daily_counts)
    text = (
        "<b>Статистика</b>\n"
        f"Колод: {stats['decks']}\n"
        f"Заметок: {stats['notes']}\n"
        f"Карточек: {stats['cards']}\n"
        f"Новые: {stats['new']}\n"
        f"В обучении: {stats['learning']}\n"
        f"К повторению сейчас: {stats['due']}\n"
        f"Приостановлено: {stats['suspended']}\n"
        f"Повторов сегодня: {stats['today_reviews']}\n"
        f"Повторов за 7 дней: {stats['week_reviews']}\n"
        f"Retention за 7 дней: {stats['week_retention']}%\n\n"
        f"<b>Последние 7 дней</b>\n{history}"
    )
    await callback.message.answer(text, reply_markup=back_to_menu())


def _parse_setting_value(field: str, raw: str) -> int | float | None:
    return validate_deck_setting_value(field, raw)  # type: ignore[return-value]


def _format_day_count(day, count: int) -> str:
    bar = "#" * min(count, 20)
    return f"{day.isoformat()}: {count} {bar}"


def _format_steps(values: list[int] | None) -> str:
    return ",".join(str(value) for value in (values or []))
