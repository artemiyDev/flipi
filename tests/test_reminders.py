import asyncio
from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import select

from bot.handlers.settings import _parse_reminder_time, _reminder_settings_text
from bot.keyboards import reminder_actions
from bot.models import Card, ReviewLog, User
from bot.services.cards import create_basic_note
from bot.services.decks import create_deck
from bot.services.reminders import select_users_to_remind, send_due_reminders
from bot.services.timezones import user_local_date
from bot.services.users import skip_reminder_today, snooze_reminder, toggle_reminders, update_reminder_time


NOW = datetime(2026, 8, 17, 20, 0, tzinfo=UTC)


async def _user(session, telegram_id: int, **values) -> User:
    defaults = {
        "timezone": "America/Sao_Paulo",
        "reminder_enabled": True,
        "reminder_minutes_local": 16 * 60,
    }
    defaults.update(values)
    user = User(
        telegram_id=telegram_id,
        **defaults,
    )
    session.add(user)
    await session.flush()
    return user


async def _due_card(session, user: User, due_at: datetime = NOW - timedelta(minutes=1)) -> Card:
    deck = await create_deck(session, user, f"Deck {user.telegram_id}")
    note = await create_basic_note(session, user, deck, "front", "back")
    card = (await session.execute(select(Card).where(Card.note_id == note.id))).scalar_one()
    card.due_at = due_at
    await session.commit()
    return card


def test_select_users_to_remind_applies_every_eligibility_condition(session_factory) -> None:
    async def check() -> None:
        async with session_factory() as session:
            eligible = await _user(session, 1)
            disabled = await _user(session, 2, reminder_enabled=False)
            no_time = await _user(session, 3, reminder_minutes_local=None)
            too_early = await _user(session, 4, reminder_minutes_local=18 * 60)
            already_sent = await _user(session, 5, reminder_last_sent_date=date(2026, 8, 17))
            skipped = await _user(session, 6, reminder_skip_date=date(2026, 8, 17))
            snoozed = await _user(session, 7, reminder_snoozed_until=NOW + timedelta(hours=1))
            due_zero = await _user(session, 8)
            studied = await _user(session, 9)
            users_with_due = [eligible, disabled, no_time, too_early, already_sent, skipped, snoozed, studied]
            cards = [await _due_card(session, user) for user in users_with_due]
            session.add(
                ReviewLog(
                    user_id=studied.id,
                    deck_id=cards[-1].deck_id,
                    card_id=cards[-1].id,
                    rating=3,
                    reviewed_at=NOW - timedelta(minutes=10),
                    elapsed_ms=100,
                    previous_due_at=NOW - timedelta(days=1),
                    next_due_at=NOW + timedelta(days=1),
                )
            )
            await session.commit()

            selected = await select_users_to_remind(session, NOW)

        assert [user.telegram_id for user in selected] == [eligible.telegram_id]
        assert due_zero.telegram_id not in [user.telegram_id for user in selected]

    asyncio.run(check())


def test_select_users_to_remind_uses_users_local_date(session_factory) -> None:
    now_utc = datetime(2026, 1, 1, 1, 0, tzinfo=UTC)

    async def check() -> None:
        async with session_factory() as session:
            user = await _user(
                session,
                10,
                reminder_minutes_local=21 * 60,
                reminder_last_sent_date=date(2025, 12, 30),
            )
            await _due_card(session, user, now_utc - timedelta(minutes=1))
            selected = await select_users_to_remind(session, now_utc)

        assert [item.telegram_id for item in selected] == [10]
        assert user_local_date(now_utc, user.timezone) == date(2025, 12, 31)

    asyncio.run(check())


def test_snooze_and_skip_delay_reminders_in_local_day(session_factory) -> None:
    async def check() -> None:
        async with session_factory() as session:
            user = await _user(session, 11)
            await _due_card(session, user)
            await snooze_reminder(session, user, NOW)
            assert await select_users_to_remind(session, NOW + timedelta(minutes=119)) == []
            assert [item.telegram_id for item in await select_users_to_remind(session, NOW + timedelta(hours=2))] == [11]
            await skip_reminder_today(session, user, user_local_date(NOW, user.timezone))
            assert await select_users_to_remind(session, NOW + timedelta(hours=2)) == []

    asyncio.run(check())


def test_sender_continues_after_failure_and_marks_every_attempt(session_factory) -> None:
    class FakeBot:
        def __init__(self) -> None:
            self.calls: list[tuple[int, str, object]] = []

        async def send_message(self, chat_id: int, text: str, **kwargs: object) -> None:
            self.calls.append((chat_id, text, kwargs["reply_markup"]))
            if chat_id == 12:
                raise RuntimeError("blocked")

    async def check() -> None:
        async with session_factory() as session:
            first = await _user(session, 12)
            second = await _user(session, 13)
            await _due_card(session, first)
            second_card = await _due_card(session, second)
            session.add_all(
                [
                    ReviewLog(
                        user_id=second.id,
                        deck_id=second_card.deck_id,
                        card_id=second_card.id,
                        rating=3,
                        reviewed_at=NOW - timedelta(days=1, minutes=minute),
                        elapsed_ms=100,
                        previous_due_at=NOW - timedelta(days=2),
                        next_due_at=NOW,
                    )
                    for minute in range(10)
                ]
            )
            await session.commit()
        bot = FakeBot()
        await send_due_reminders(bot, session_factory, "https://example.test/app", NOW)
        async with session_factory() as session:
            users = (await session.execute(select(User).where(User.telegram_id.in_([12, 13])))).scalars()
            sent_dates = {user.telegram_id: user.reminder_last_sent_date for user in users}

        assert [call[0] for call in bot.calls] == [12, 13]
        assert "1 карточек" in bot.calls[1][1]
        assert "~1 мин" in bot.calls[1][1]
        assert "Серия: 1 дней" in bot.calls[1][1]
        assert bot.calls[1][2].inline_keyboard[0][0].web_app is not None
        assert sent_dates == {12: date(2026, 8, 17), 13: date(2026, 8, 17)}

    asyncio.run(check())


def test_reminder_settings_accept_valid_time_and_web_app_button(session_factory) -> None:
    async def check() -> None:
        async with session_factory() as session:
            user = await _user(session, 14, reminder_enabled=False, reminder_minutes_local=None)
            assert await toggle_reminders(session, user) is True
            await update_reminder_time(session, user, 8 * 60 + 5)
            assert user.reminder_minutes_local == 485

    asyncio.run(check())
    assert _parse_reminder_time("08:05") == 485
    assert _parse_reminder_time("8:05") is None
    assert _parse_reminder_time("24:00") is None
    assert _reminder_settings_text(True, 485) == "Напоминания: Каждый день в 08:05"
    assert _reminder_settings_text(False, 485) == "Напоминания: Выключены"
    assert reminder_actions("https://example.test/app").inline_keyboard[0][0].web_app is not None
