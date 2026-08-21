from datetime import UTC, date, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from bot.models import Card, Deck, Note, ReviewLog, User
from bot.services.decks import deck_list_with_counts
from bot.services.goals import STREAK_TARGET
from bot.services.study import count_done_today
from bot.services.timezones import user_day_start_utc, user_local_date, user_today


async def user_stats(session: AsyncSession, user: User) -> dict[str, int | float]:
    now = datetime.now(UTC)
    today_start = user_day_start_utc(user.timezone)
    week_start = now - timedelta(days=7)

    stats: dict[str, int | float] = {}
    stats["decks"] = await _count(session, select(func.count(Deck.id)).where(Deck.user_id == user.id))
    stats["notes"] = await _count(session, select(func.count(Note.id)).where(Note.user_id == user.id))
    stats["cards"] = await _count(session, select(func.count(Card.id)).where(Card.user_id == user.id))
    stats["new"] = await _count(
        session,
        select(func.count(Card.id)).where(Card.user_id == user.id, Card.state == "new"),
    )
    stats["learning"] = await _count(
        session,
        select(func.count(Card.id)).where(
            Card.user_id == user.id,
            Card.state.in_(["learning", "relearning"]),
        ),
    )
    stats["due"] = await _count(
        session,
        select(func.count(Card.id)).where(
            Card.user_id == user.id,
            Card.state == "review",
            Card.due_at <= now,
            Card.suspended.is_(False),
        ),
    )
    stats["suspended"] = await _count(
        session,
        select(func.count(Card.id)).where(Card.user_id == user.id, Card.suspended.is_(True)),
    )
    stats["today_reviews"] = await _count(
        session,
        select(func.count(ReviewLog.id)).where(
            ReviewLog.user_id == user.id,
            ReviewLog.reviewed_at >= today_start,
        ),
    )
    stats["week_reviews"] = await _count(
        session,
        select(func.count(ReviewLog.id)).where(
            ReviewLog.user_id == user.id,
            ReviewLog.reviewed_at >= week_start,
        ),
    )
    week_success = await _count(
        session,
        select(func.count(ReviewLog.id)).where(
            ReviewLog.user_id == user.id,
            ReviewLog.reviewed_at >= week_start,
            ReviewLog.rating.in_([2, 3, 4]),
        ),
    )
    stats["week_retention"] = round(
        (week_success / stats["week_reviews"] * 100) if stats["week_reviews"] else 0,
        1,
    )
    return stats


async def daily_review_counts(
    session: AsyncSession,
    user: User,
    days: int = 7,
) -> list[tuple[date, int]]:
    today = user_today(user.timezone)
    start_day = today - timedelta(days=days - 1)
    result = await session.execute(
        select(ReviewLog.reviewed_at).where(
            ReviewLog.user_id == user.id,
            ReviewLog.reviewed_at >= user_day_start_utc(user.timezone, start_day),
        )
        .order_by(ReviewLog.reviewed_at.asc())
    )
    raw_counts: dict[date, int] = {}
    for (reviewed_at,) in result.all():
        local_day = user_local_date(reviewed_at, user.timezone)
        raw_counts[local_day] = raw_counts.get(local_day, 0) + 1
    return [
        (start_day + timedelta(days=offset), raw_counts.get(start_day + timedelta(days=offset), 0))
        for offset in range(days)
    ]


async def deck_review_stats(session: AsyncSession, deck: Deck) -> dict[str, int | float]:
    now = datetime.now(UTC)
    week_start = now - timedelta(days=7)
    total = await _count(
        session,
        select(func.count(ReviewLog.id)).where(
            ReviewLog.deck_id == deck.id,
            ReviewLog.reviewed_at >= week_start,
        ),
    )
    success = await _count(
        session,
        select(func.count(ReviewLog.id)).where(
            ReviewLog.deck_id == deck.id,
            ReviewLog.reviewed_at >= week_start,
            ReviewLog.rating.in_([2, 3, 4]),
        ),
    )
    return {
        "week_reviews": total,
        "week_retention": round((success / total * 100) if total else 0, 1),
    }


async def stats_overview(session: AsyncSession, user: User) -> dict:
    today = user_today(user.timezone)
    period_start = user_day_start_utc(user.timezone, today - timedelta(days=29))
    rating_counts = {"again": 0, "hard": 0, "good": 0, "easy": 0}
    rating_names = {1: "again", 2: "hard", 3: "good", 4: "easy"}

    ratings_result = await session.execute(
        select(ReviewLog.rating, func.count(ReviewLog.id))
        .where(
            ReviewLog.user_id == user.id,
            ReviewLog.reviewed_at >= period_start,
        )
        .group_by(ReviewLog.rating)
    )
    for rating, count in ratings_result.all():
        rating_name = rating_names.get(rating)
        if rating_name is not None:
            rating_counts[rating_name] = int(count)

    total_reviews = sum(rating_counts.values())
    successful_reviews = rating_counts["hard"] + rating_counts["good"] + rating_counts["easy"]
    due_now = sum(sum(row[2:]) for row in await deck_list_with_counts(session, user))

    return {
        "due_now": due_now,
        "done_today": await count_done_today(session, user),
        "streak_days": await streak_days(session, user, today),
        "retention_30d": successful_reviews / total_reviews if total_reviews else None,
        "ratings_30d": rating_counts,
    }


async def heatmap_review_counts(
    session: AsyncSession,
    user: User,
    weeks: int,
) -> list[tuple[date, int]]:
    today = user_today(user.timezone)
    start_day = today - timedelta(days=weeks * 7 - 1)
    end_day = today + timedelta(days=1)
    reviewed_at_result = await session.execute(
        select(ReviewLog.reviewed_at).where(
            ReviewLog.user_id == user.id,
            ReviewLog.reviewed_at >= user_day_start_utc(user.timezone, start_day),
            ReviewLog.reviewed_at < user_day_start_utc(user.timezone, end_day),
        )
    )
    counts: dict[date, int] = {}
    for (reviewed_at,) in reviewed_at_result.all():
        local_day = user_local_date(reviewed_at, user.timezone)
        counts[local_day] = counts.get(local_day, 0) + 1
    return sorted(counts.items())


async def forecast_due_counts(
    session: AsyncSession,
    user: User,
    days: int,
) -> tuple[int, list[tuple[date, int]]]:
    today = user_today(user.timezone)
    today_start = user_day_start_utc(user.timezone, today)
    forecast_end = user_day_start_utc(user.timezone, today + timedelta(days=days))
    active_card_conditions = (
        Card.user_id == user.id,
        Card.suspended.is_(False),
        Card.state.in_(["review", "learning", "relearning"]),
        Deck.is_archived.is_(False),
    )

    overdue_result = await session.execute(
        select(func.count(Card.id))
        .join(Deck, Card.deck_id == Deck.id)
        .where(*active_card_conditions, Card.due_at < today_start)
    )
    due_at_result = await session.execute(
        select(Card.due_at)
        .join(Deck, Card.deck_id == Deck.id)
        .where(*active_card_conditions, Card.due_at >= today_start, Card.due_at < forecast_end)
    )
    counts: dict[date, int] = {}
    for (due_at,) in due_at_result.all():
        local_day = user_local_date(due_at, user.timezone)
        counts[local_day] = counts.get(local_day, 0) + 1
    return int(overdue_result.scalar_one()), sorted(counts.items())


async def streak_days(session: AsyncSession, user: User, today: date) -> int:
    reviewed_at_result = await session.execute(
        select(ReviewLog.reviewed_at).where(
            ReviewLog.user_id == user.id,
            ReviewLog.rating.in_([2, 3, 4]),
        )
    )
    successful_by_day: dict[date, int] = {}
    for (reviewed_at,) in reviewed_at_result.all():
        local_day = user_local_date(reviewed_at, user.timezone)
        successful_by_day[local_day] = successful_by_day.get(local_day, 0) + 1

    achieved_days = {
        local_day
        for local_day, success_count in successful_by_day.items()
        if success_count >= STREAK_TARGET
    }
    current_day = today if today in achieved_days else today - timedelta(days=1)
    streak = 0
    while current_day in achieved_days:
        streak += 1
        current_day -= timedelta(days=1)
    return streak


async def _count(session: AsyncSession, query) -> int:
    result = await session.execute(query)
    return int(result.scalar_one())
