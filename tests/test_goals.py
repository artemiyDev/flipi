import asyncio
from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncSession

from bot.models import Card, DailyStudyCounter, Deck, Note, ReviewLog, User
from bot.services.goals import daily_goal_progress, due_today_remaining, successful_today
from bot.services.stats import streak_days


def local_datetime(day: date, timezone_name: str, hour: int = 12, minute: int = 0) -> datetime:
    return datetime.combine(day, time(hour, minute), tzinfo=ZoneInfo(timezone_name)).astimezone(
        UTC
    )


async def create_user(session: AsyncSession, timezone_name: str = "UTC") -> User:
    user = User(telegram_id=987654321, timezone=timezone_name)
    session.add(user)
    await session.flush()
    return user


async def create_deck(
    session: AsyncSession,
    user: User,
    name: str,
    *,
    new_limit: int = 20,
    review_limit: int = 200,
    archived: bool = False,
) -> Deck:
    deck = Deck(
        user_id=user.id,
        name=name,
        new_cards_per_day=new_limit,
        reviews_per_day=review_limit,
        is_archived=archived,
    )
    session.add(deck)
    await session.flush()
    return deck


async def create_card(
    session: AsyncSession,
    user: User,
    deck: Deck,
    name: str,
    state: str,
    due_at: datetime,
    *,
    suspended: bool = False,
    buried_until: date | None = None,
) -> Card:
    note = Note(
        user_id=user.id,
        deck_id=deck.id,
        front=name,
        back=name,
    )
    session.add(note)
    await session.flush()
    card = Card(
        user_id=user.id,
        deck_id=deck.id,
        note_id=note.id,
        state=state,
        due_at=due_at,
        suspended=suspended,
        buried_until=buried_until,
    )
    session.add(card)
    await session.flush()
    return card


def add_review(
    session: AsyncSession,
    card: Card,
    reviewed_at: datetime,
    rating: int,
) -> None:
    session.add(
        ReviewLog(
            user_id=card.user_id,
            deck_id=card.deck_id,
            card_id=card.id,
            rating=rating,
            reviewed_at=reviewed_at,
            elapsed_ms=100,
            previous_due_at=reviewed_at,
            next_due_at=reviewed_at + timedelta(days=1),
        )
    )


def test_successful_today_uses_local_boundaries_and_reaches_exact_target(session_factory) -> None:
    async def run() -> None:
        async with session_factory() as session:
            timezone_name = "Asia/Tokyo"
            user = await create_user(session, timezone_name)
            deck = await create_deck(session, user, "Japanese")
            card = await create_card(
                session,
                user,
                deck,
                "card",
                "review",
                datetime(2026, 1, 2, tzinfo=UTC),
            )
            now = datetime(2026, 1, 2, 1, 0, tzinfo=UTC)

            add_review(session, card, datetime(2026, 1, 1, 23, 55, tzinfo=UTC), 2)
            add_review(session, card, datetime(2026, 1, 2, 0, 5, tzinfo=UTC), 4)
            add_review(session, card, datetime(2026, 1, 1, 14, 59, tzinfo=UTC), 3)
            add_review(session, card, datetime(2026, 1, 2, 15, 0, tzinfo=UTC), 3)
            add_review(session, card, datetime(2026, 1, 2, 0, 10, tzinfo=UTC), 1)
            for offset in range(8):
                add_review(
                    session,
                    card,
                    datetime(2026, 1, 2, 0, 20 + offset, tzinfo=UTC),
                    3,
                )
            await session.commit()

            assert await successful_today(session, user, now) == 10
            assert await daily_goal_progress(session, user, now) == {
                "streak": {"done": 10, "target": 10, "achieved": True},
                "full": {"remaining": 1, "achieved": False},
            }

    asyncio.run(run())


def test_daily_goal_progress_ignores_again_before_tenth_success(session_factory) -> None:
    async def run() -> None:
        async with session_factory() as session:
            user = await create_user(session)
            deck = await create_deck(session, user, "Goal")
            card = await create_card(
                session,
                user,
                deck,
                "card",
                "review",
                datetime(2026, 4, 10, tzinfo=UTC),
            )
            now = datetime(2026, 4, 10, 12, tzinfo=UTC)
            for offset in range(9):
                add_review(session, card, now - timedelta(minutes=offset), 3)
            for offset in range(20):
                add_review(session, card, now - timedelta(hours=1, minutes=offset), 1)
            await session.commit()

            progress = await daily_goal_progress(session, user, now)
            assert progress["streak"] == {"done": 9, "target": 10, "achieved": False}

            add_review(session, card, now - timedelta(hours=2), 2)
            await session.commit()
            progress = await daily_goal_progress(session, user, now)
            assert progress["streak"] == {"done": 10, "target": 10, "achieved": True}

            add_review(session, card, now - timedelta(hours=3), 4)
            await session.commit()
            progress = await daily_goal_progress(session, user, now)
            assert progress["streak"] == {"done": 10, "target": 10, "achieved": True}

    asyncio.run(run())


def test_due_today_remaining_applies_global_limits_and_active_filters(session_factory) -> None:
    async def run() -> None:
        async with session_factory() as session:
            timezone_name = "America/Sao_Paulo"
            user = await create_user(session, timezone_name)
            today = date(2026, 3, 10)
            now = local_datetime(today, timezone_name, 9)
            before_midnight = local_datetime(today, timezone_name, 23, 59)
            at_midnight = local_datetime(today + timedelta(days=1), timezone_name, 0)
            active = await create_deck(
                session,
                user,
                "Active",
                new_limit=3,
                review_limit=2,
            )
            second = await create_deck(
                session,
                user,
                "Second",
                new_limit=1,
                review_limit=2,
            )
            archived = await create_deck(session, user, "Archived", archived=True)
            exhausted = await create_deck(
                session,
                user,
                "Exhausted",
                new_limit=1,
                review_limit=1,
            )
            stale = await create_deck(
                session,
                user,
                "Stale counter",
                new_limit=1,
                review_limit=1,
            )
            session.add(
                DailyStudyCounter(
                    user_id=user.id,
                    deck_id=active.id,
                    study_date=today,
                    new_seen=1,
                    reviews_done=1,
                )
            )
            session.add_all(
                [
                    DailyStudyCounter(
                        user_id=user.id,
                        deck_id=exhausted.id,
                        study_date=today,
                        new_seen=3,
                        reviews_done=4,
                    ),
                    DailyStudyCounter(
                        user_id=user.id,
                        deck_id=stale.id,
                        study_date=today - timedelta(days=1),
                        new_seen=9,
                        reviews_done=9,
                    ),
                ]
            )

            for index in range(4):
                await create_card(session, user, active, f"new-{index}", "new", now)
            for index in range(3):
                await create_card(session, user, active, f"review-{index}", "review", now)
            await create_card(session, user, active, "learning", "learning", now)
            await create_card(session, user, active, "relearning", "relearning", now)
            await create_card(
                session, user, active, "before-midnight", "learning", before_midnight
            )
            await create_card(session, user, active, "at-midnight", "learning", at_midnight)
            await create_card(
                session, user, active, "suspended", "learning", now, suspended=True
            )
            await create_card(
                session,
                user,
                active,
                "buried-today",
                "learning",
                now,
                buried_until=today,
            )
            await create_card(
                session,
                user,
                active,
                "buried-yesterday",
                "learning",
                now,
                buried_until=today - timedelta(days=1),
            )
            await create_card(
                session,
                user,
                active,
                "buried-tomorrow",
                "learning",
                now,
                buried_until=today + timedelta(days=1),
            )

            for index in range(2):
                await create_card(session, user, second, f"second-new-{index}", "new", now)
            for index in range(3):
                await create_card(
                    session, user, second, f"second-review-{index}", "review", now
                )
            await create_card(session, user, second, "second-learning", "learning", now)
            await create_card(session, user, archived, "archived", "learning", now)
            await create_card(session, user, exhausted, "exhausted-new", "new", now)
            await create_card(session, user, exhausted, "exhausted-review", "review", now)
            await create_card(session, user, stale, "stale-new", "new", now)
            await create_card(session, user, stale, "stale-review", "review", now)
            await session.commit()

            statement_count = 0

            def count_statement(*args) -> None:
                nonlocal statement_count
                statement_count += 1

            engine = session.bind
            event.listen(engine.sync_engine, "before_cursor_execute", count_statement)
            try:
                remaining = await due_today_remaining(session, user, now)
            finally:
                event.remove(engine.sync_engine, "before_cursor_execute", count_statement)

            assert remaining == 13
            assert statement_count == 1

    asyncio.run(run())


def test_due_today_remaining_uses_dst_local_midnight_boundary(session_factory) -> None:
    async def run() -> None:
        async with session_factory() as session:
            timezone_name = "America/New_York"
            user = await create_user(session, timezone_name)
            deck = await create_deck(session, user, "DST")
            now = datetime(2026, 3, 8, 16, tzinfo=UTC)
            await create_card(
                session,
                user,
                deck,
                "before-midnight",
                "relearning",
                datetime(2026, 3, 9, 3, 59, tzinfo=UTC),
            )
            await create_card(
                session,
                user,
                deck,
                "at-midnight",
                "relearning",
                datetime(2026, 3, 9, 4, 0, tzinfo=UTC),
            )
            await session.commit()

            assert await due_today_remaining(session, user, now) == 1

    asyncio.run(run())


def test_streak_requires_ten_successes_and_starts_from_yesterday_when_today_open(
    session_factory,
) -> None:
    async def run() -> None:
        async with session_factory() as session:
            timezone_name = "America/Los_Angeles"
            user = await create_user(session, timezone_name)
            deck = await create_deck(session, user, "Streak")
            today = date(2026, 5, 20)
            card = await create_card(
                session,
                user,
                deck,
                "card",
                "review",
                local_datetime(today, timezone_name),
            )
            successful_counts = {
                today: 9,
                today - timedelta(days=1): 10,
                today - timedelta(days=2): 10,
            }
            for day, count in successful_counts.items():
                for offset in range(count):
                    add_review(
                        session,
                        card,
                        local_datetime(day, timezone_name, 12, offset),
                        3,
                    )
            for offset in range(20):
                add_review(
                    session,
                    card,
                    local_datetime(today, timezone_name, 13, offset),
                    1,
                )
            for offset in range(9):
                add_review(
                    session,
                    card,
                    local_datetime(today - timedelta(days=3), timezone_name, 12, offset),
                    4,
                )
            await session.commit()

            assert await streak_days(session, user, today) == 2

            add_review(session, card, local_datetime(today, timezone_name, 14), 2)
            await session.commit()
            assert await streak_days(session, user, today) == 3

    asyncio.run(run())
