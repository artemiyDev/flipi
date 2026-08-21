from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from bot.models import Card, ReviewLog, User
from bot.services.events import track

LEECH_THRESHOLD = 4
LEECH_REPEAT_INTERVAL = 2


class LeechResumeConflictError(ValueError):
    pass


@dataclass(frozen=True)
class LeechResumeResult:
    review_lapses: int
    replayed: bool


def is_leech(card: Card) -> bool:
    return (card.review_lapses or 0) >= LEECH_THRESHOLD


def is_leech_alert_count(review_lapses: int) -> bool:
    return (
        review_lapses >= LEECH_THRESHOLD
        and (review_lapses - LEECH_THRESHOLD) % LEECH_REPEAT_INTERVAL == 0
    )


def register_review_lapse(
    card: Card,
    review: ReviewLog,
    previous_state: str,
    rating: int,
) -> int | None:
    review.leech_alert_lapses = None
    if previous_state != "review" or rating != 1:
        return None

    card.review_lapses = (card.review_lapses or 0) + 1
    if not is_leech_alert_count(card.review_lapses):
        return None

    review.leech_alert_lapses = card.review_lapses
    card.suspended = True
    card.leech_suspended_lapses = card.review_lapses
    card.buried_until = None
    return card.review_lapses


async def resume_leech(
    session: AsyncSession,
    user: User,
    card_id: int,
    expected_review_lapses: int,
) -> LeechResumeResult | None:
    result = await session.execute(
        select(Card)
        .where(Card.id == card_id, Card.user_id == user.id)
        .execution_options(populate_existing=True)
        .with_for_update()
    )
    card = result.scalar_one_or_none()
    if card is None:
        return None

    if not is_leech_alert_count(expected_review_lapses):
        raise LeechResumeConflictError("Leech state has changed")

    if (
        not card.suspended
        and card.leech_suspended_lapses is None
        and card.review_lapses == expected_review_lapses
    ):
        return LeechResumeResult(review_lapses=card.review_lapses, replayed=True)

    if (
        card.review_lapses != expected_review_lapses
        or card.leech_suspended_lapses != expected_review_lapses
        or not card.suspended
    ):
        raise LeechResumeConflictError("Leech state has changed")

    card.suspended = False
    card.leech_suspended_lapses = None
    await track(
        session,
        user.id,
        "leech_resumed",
        review_lapses=card.review_lapses,
    )
    await session.commit()
    return LeechResumeResult(review_lapses=card.review_lapses, replayed=False)


async def defer_leech(
    session: AsyncSession,
    user: User,
    card_id: int,
    expected_review_lapses: int,
) -> LeechResumeResult | None:
    result = await session.execute(
        select(Card)
        .where(Card.id == card_id, Card.user_id == user.id)
        .execution_options(populate_existing=True)
        .with_for_update()
    )
    card = result.scalar_one_or_none()
    if card is None:
        return None

    if not is_leech_alert_count(expected_review_lapses):
        raise LeechResumeConflictError("Leech state has changed")

    if (
        card.suspended
        and card.leech_suspended_lapses is None
        and card.review_lapses == expected_review_lapses
    ):
        return LeechResumeResult(review_lapses=card.review_lapses, replayed=True)

    if (
        not card.suspended
        or card.review_lapses != expected_review_lapses
        or card.leech_suspended_lapses != expected_review_lapses
    ):
        raise LeechResumeConflictError("Leech state has changed")

    card.leech_suspended_lapses = None
    await track(
        session,
        user.id,
        "leech_deferred",
        review_lapses=card.review_lapses,
    )
    await session.commit()
    return LeechResumeResult(review_lapses=card.review_lapses, replayed=False)
