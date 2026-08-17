import asyncio
import logging
from datetime import UTC, datetime
from typing import Protocol

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from bot.keyboards import reminder_actions
from bot.models import Card, Deck, User
from bot.services.stats import streak_days
from bot.services.study import count_done_today
from bot.services.timezones import user_local_datetime

logger = logging.getLogger(__name__)


class ReminderBot(Protocol):
    async def send_message(self, chat_id: int, text: str, **kwargs: object) -> object: ...


def reminder_text(due_count: int, streak: int) -> str:
    minutes = max(1, (due_count * 20 + 59) // 60)
    text = f"🧠 К повторению: <b>{due_count} карточек</b> (~{minutes} мин)"
    if streak > 0:
        text += f"\nСерия: {streak} дней"
    return text


async def select_users_to_remind(session: AsyncSession, now_utc: datetime) -> list[User]:
    now_utc = _utc(now_utc)
    result = await session.execute(
        select(User).where(
            User.reminder_enabled.is_(True),
            User.reminder_minutes_local.is_not(None),
        )
    )
    users: list[User] = []
    for user in result.scalars():
        local_now = user_local_datetime(now_utc, user.timezone)
        local_today = local_now.date()
        reminder_minutes = user.reminder_minutes_local
        if reminder_minutes is None or local_now.hour * 60 + local_now.minute < reminder_minutes:
            continue
        if user.reminder_last_sent_date == local_today:
            continue
        if user.reminder_skip_date == local_today:
            continue
        if (
            user.reminder_snoozed_until is not None
            and _utc(user.reminder_snoozed_until) > now_utc
        ):
            continue
        if await due_cards_count(session, user, now_utc) == 0:
            continue
        if await count_done_today(session, user, now_utc) != 0:
            continue
        users.append(user)
    return users


async def due_cards_count(session: AsyncSession, user: User, now_utc: datetime) -> int:
    result = await session.execute(
        select(func.count(Card.id))
        .join(Deck, Card.deck_id == Deck.id)
        .where(
            Card.user_id == user.id,
            Card.suspended.is_(False),
            Card.due_at <= _utc(now_utc),
            Card.state.in_(("new", "learning", "relearning", "review")),
            Deck.is_archived.is_(False),
        )
    )
    return int(result.scalar_one())


async def send_due_reminders(
    bot: ReminderBot,
    session_factory: async_sessionmaker[AsyncSession],
    web_app_url: str,
    now_utc: datetime | None = None,
) -> None:
    now_utc = _utc(now_utc or datetime.now(UTC))
    async with session_factory() as session:
        users = await select_users_to_remind(session, now_utc)
        for user in users:
            local_today = user_local_datetime(now_utc, user.timezone).date()
            due_count = await due_cards_count(session, user, now_utc)
            try:
                await bot.send_message(
                    user.telegram_id,
                    reminder_text(due_count, await streak_days(session, user, local_today)),
                    reply_markup=reminder_actions(web_app_url),
                )
            except Exception:
                logger.exception("Unable to send reminder to user %s", user.id)
            user.reminder_last_sent_date = local_today
        await session.commit()


async def run_reminder_loop(
    bot: ReminderBot,
    session_factory: async_sessionmaker[AsyncSession],
    web_app_url: str,
) -> None:
    while True:
        try:
            await send_due_reminders(bot, session_factory, web_app_url)
        except Exception:
            logger.exception("Reminder cycle failed")
        await asyncio.sleep(60)


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)
