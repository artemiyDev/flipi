import asyncio
import json
from datetime import UTC, datetime

from fsrs import ReviewLog as FsrsReviewLog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from bot.models import Deck, ReviewLog, User

MINIMUM_REVIEW_COUNT = 400


class InsufficientHistoryError(ValueError):
    def __init__(self, review_count: int) -> None:
        self.review_count = review_count
        super().__init__(f"At least {MINIMUM_REVIEW_COUNT} reviews are required")


class OptimizerUnavailableError(RuntimeError):
    """Raised when the optional FSRS optimizer dependencies are unavailable."""


def _review_log_json(value: dict | str) -> str:
    return value if isinstance(value, str) else json.dumps(value)


async def collect_deck_review_logs(session: AsyncSession, deck: Deck) -> list[FsrsReviewLog]:
    result = await session.scalars(
        select(ReviewLog.fsrs_review_log)
        .where(
            ReviewLog.deck_id == deck.id,
            ReviewLog.fsrs_review_log.is_not(None),
        )
        .order_by(ReviewLog.reviewed_at, ReviewLog.id)
    )
    return [FsrsReviewLog.from_json(_review_log_json(value)) for value in result.all()]


def _compute_optimal_parameters(review_logs: list[FsrsReviewLog]) -> list[float]:
    from fsrs import Optimizer

    return Optimizer(review_logs).compute_optimal_parameters()


async def optimize_deck(session: AsyncSession, user: User, deck: Deck) -> dict:
    review_logs = await collect_deck_review_logs(session, deck)
    review_count = len(review_logs)
    if review_count < MINIMUM_REVIEW_COUNT:
        raise InsufficientHistoryError(review_count)

    try:
        parameters = await asyncio.to_thread(_compute_optimal_parameters, review_logs)
    except ImportError as exc:
        raise OptimizerUnavailableError from exc

    optimized_at = datetime.now(UTC)
    deck.fsrs_parameters = parameters
    deck.fsrs_optimized_at = optimized_at
    await session.flush()
    return {"review_count": review_count, "optimized_at": optimized_at}
