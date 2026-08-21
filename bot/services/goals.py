from datetime import UTC, datetime, timedelta

from sqlalchemy import and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from bot.models import Card, DailyStudyCounter, Deck, ReviewLog, User
from bot.services.timezones import user_day_start_utc, user_local_date

STREAK_TARGET = 10


async def successful_today(
    session: AsyncSession,
    user: User,
    now_utc: datetime | None = None,
) -> int:
    now = now_utc or datetime.now(UTC)
    today = user_local_date(now, user.timezone)
    today_start = user_day_start_utc(user.timezone, today)
    tomorrow_start = user_day_start_utc(user.timezone, today + timedelta(days=1))
    result = await session.execute(
        select(func.count(ReviewLog.id)).where(
            ReviewLog.user_id == user.id,
            ReviewLog.rating.in_([2, 3, 4]),
            ReviewLog.reviewed_at >= today_start,
            ReviewLog.reviewed_at < tomorrow_start,
        )
    )
    return int(result.scalar_one())


async def due_today_remaining(
    session: AsyncSession,
    user: User,
    now_utc: datetime | None = None,
) -> int:
    now = now_utc or datetime.now(UTC)
    today = user_local_date(now, user.timezone)
    tomorrow_start = user_day_start_utc(user.timezone, today + timedelta(days=1))
    new_seen = func.coalesce(DailyStudyCounter.new_seen, 0)
    reviews_done = func.coalesce(DailyStudyCounter.reviews_done, 0)

    result = await session.execute(
        select(
            Card.state,
            func.count(Card.id),
            Deck.new_cards_per_day,
            Deck.reviews_per_day,
            new_seen,
            reviews_done,
        )
        .join(Deck, Card.deck_id == Deck.id)
        .outerjoin(
            DailyStudyCounter,
            and_(
                DailyStudyCounter.user_id == user.id,
                DailyStudyCounter.deck_id == Deck.id,
                DailyStudyCounter.study_date == today,
            ),
        )
        .where(
            Card.user_id == user.id,
            Deck.user_id == user.id,
            Deck.is_archived.is_(False),
            Card.suspended.is_(False),
            or_(Card.buried_until.is_(None), Card.buried_until < today),
            Card.due_at < tomorrow_start,
            Card.state.in_(["new", "learning", "relearning", "review"]),
        )
        .group_by(
            Deck.id,
            Card.state,
            Deck.new_cards_per_day,
            Deck.reviews_per_day,
            new_seen,
            reviews_done,
        )
    )

    remaining = 0
    for state, card_count, new_limit, review_limit, used_new, used_reviews in result.all():
        count = int(card_count)
        if state in {"learning", "relearning"}:
            remaining += count
        elif state == "new":
            remaining += min(count, max(int(new_limit) - int(used_new), 0))
        elif state == "review":
            remaining += min(count, max(int(review_limit) - int(used_reviews), 0))
    return remaining


async def daily_goal_progress(
    session: AsyncSession,
    user: User,
    now_utc: datetime | None = None,
) -> dict[str, dict[str, int | bool]]:
    now = now_utc or datetime.now(UTC)
    successful_count = await successful_today(session, user, now)
    remaining = await due_today_remaining(session, user, now)
    return {
        "streak": {
            "done": min(successful_count, STREAK_TARGET),
            "target": STREAK_TARGET,
            "achieved": successful_count >= STREAK_TARGET,
        },
        "full": {
            "remaining": remaining,
            "achieved": remaining == 0,
        },
    }
