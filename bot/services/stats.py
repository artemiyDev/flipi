from datetime import UTC, date, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from bot.models import Card, Deck, Note, ReviewLog, User
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


async def _count(session: AsyncSession, query) -> int:
    result = await session.execute(query)
    return int(result.scalar_one())
