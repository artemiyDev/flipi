from datetime import UTC, datetime

from aiogram import F, Router
from aiogram.types import CallbackQuery

from bot.db import async_session
from bot.services.timezones import user_local_date
from bot.services.users import get_or_create_user, skip_reminder_today, snooze_reminder

router = Router()


@router.callback_query(F.data == "reminder:snooze")
async def snooze(callback: CallbackQuery) -> None:
    if callback.from_user is None:
        return
    now_utc = datetime.now(UTC)
    async with async_session() as session:
        user = await get_or_create_user(session, callback.from_user)
        await snooze_reminder(session, user, now_utc)
    await callback.answer("Напомню через 2 часа")


@router.callback_query(F.data == "reminder:skip")
async def skip_today(callback: CallbackQuery) -> None:
    if callback.from_user is None:
        return
    now_utc = datetime.now(UTC)
    async with async_session() as session:
        user = await get_or_create_user(session, callback.from_user)
        await skip_reminder_today(session, user, user_local_date(now_utc, user.timezone))
    await callback.answer("Хорошо, до завтра")
