import re
from dataclasses import dataclass
from datetime import datetime

import nh3
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from bot.models import Card, ReviewLog, User
from bot.services.cards import (
    bury_sibling_cards,
    get_next_due_card,
    increment_daily_counter,
)
from bot.services.decks import list_user_decks
from bot.services.events import track
from bot.services.leeches import register_review_lapse
from bot.services.scheduler import review_with_fsrs
from bot.services.timezones import user_day_start_utc, user_local_date

HTML_TAGS = {
    "b",
    "i",
    "u",
    "s",
    "em",
    "strong",
    "sub",
    "sup",
    "span",
    "div",
    "p",
    "br",
    "hr",
    "ul",
    "ol",
    "li",
    "table",
    "thead",
    "tbody",
    "tfoot",
    "tr",
    "th",
    "td",
    "img",
}
MAX_CARD_CSS_BYTES = 64 * 1024
IMPORT_CSS_RE = re.compile(r"@import\s+[^;]+;", flags=re.IGNORECASE)
CSS_URL_RE = re.compile(r"url\(\s*(.*?)\s*\)", flags=re.IGNORECASE | re.DOTALL)


class AnswerRequestConflictError(ValueError):
    pass


class SuspendedCardAnswerError(ValueError):
    pass


@dataclass(frozen=True)
class StudyAnswerResult:
    state: str
    due_at: datetime
    replayed: bool
    leech_alert_lapses: int | None


def sanitize_card_html(value: str) -> str:
    def allow_attribute(tag: str, attribute: str, content: str) -> str | None:
        if tag == "img" and attribute == "src" and content.startswith("/api/media/"):
            return content
        return None

    sanitized = nh3.clean(
        value,
        tags=HTML_TAGS,
        clean_content_tags={"script", "style", "iframe"},
        attributes={"img": {"src"}},
        attribute_filter=allow_attribute,
    )
    return re.sub(r"<img>", "", sanitized)


def sanitize_card_css(value: str) -> str:
    clipped = value.encode("utf-8")[:MAX_CARD_CSS_BYTES].decode("utf-8", "ignore")
    without_imports = IMPORT_CSS_RE.sub("", clipped)

    def replace_url(match: re.Match[str]) -> str:
        target = match.group(1).strip()
        if len(target) >= 2 and target[0] in "\"'" and target[-1] == target[0]:
            target = target[1:-1].strip()
        if target.lower().startswith("data:") or target.startswith("/api/media/"):
            return match.group(0)
        return ""

    sanitized = CSS_URL_RE.sub(replace_url, without_imports)
    sanitized = re.sub(r"expression\s*\(", "", sanitized, flags=re.IGNORECASE)
    return re.sub(r"behavior\s*:", "", sanitized, flags=re.IGNORECASE)


async def get_next_card_for_user(session: AsyncSession, user: User) -> Card | None:
    for deck in await list_user_decks(session, user):
        card = await get_next_due_card(session, deck, user.timezone)
        if card is not None:
            return card
    return None


async def answer_card(
    session: AsyncSession,
    user: User,
    card: Card,
    rating: int,
    elapsed_ms: int | None = None,
) -> Card:
    result = await session.execute(
        select(Card)
        .where(Card.id == card.id, Card.user_id == user.id)
        .options(selectinload(Card.deck))
        .execution_options(populate_existing=True)
        .with_for_update()
    )
    locked_card = result.scalar_one_or_none()
    if locked_card is None:
        raise LookupError("Card not found")
    if locked_card.suspended:
        raise SuspendedCardAnswerError("Card is suspended")
    await _record_answer(session, user, locked_card, rating, elapsed_ms)
    await session.commit()
    return locked_card


async def answer_card_request(
    session: AsyncSession,
    user: User,
    card_id: int,
    rating: int,
    elapsed_ms: int | None = None,
    request_id: str | None = None,
) -> StudyAnswerResult | None:
    user_id = user.id
    timezone_name = user.timezone

    result = await session.execute(
        select(Card)
        .where(Card.id == card_id, Card.user_id == user_id)
        .options(selectinload(Card.deck))
        .execution_options(populate_existing=True)
        .with_for_update()
    )
    card = result.scalar_one_or_none()
    if card is None:
        return None

    if request_id is not None:
        existing = await _get_review_by_request_id(session, user_id, request_id)
        if existing is not None:
            return _replay_review(existing, card_id, rating)

    if card.suspended:
        raise SuspendedCardAnswerError("Card is suspended")

    try:
        review = await _record_answer(
            session,
            user,
            card,
            rating,
            elapsed_ms,
            request_id=request_id,
            timezone_name=timezone_name,
        )
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        if request_id is None:
            raise
        existing = await _get_review_by_request_id(session, user_id, request_id)
        if existing is None:
            raise
        try:
            return _replay_review(existing, card_id, rating)
        except AnswerRequestConflictError as conflict:
            raise conflict from exc

    return StudyAnswerResult(
        state=card.state,
        due_at=card.due_at,
        replayed=False,
        leech_alert_lapses=review.leech_alert_lapses,
    )


async def _record_answer(
    session: AsyncSession,
    user: User,
    card: Card,
    rating: int,
    elapsed_ms: int | None,
    *,
    request_id: str | None = None,
    timezone_name: str | None = None,
) -> ReviewLog:
    previous_state = card.state
    review = review_with_fsrs(card, card.deck, rating, elapsed_ms)
    review.request_id = request_id
    review.state_after = card.state
    leech_alert_lapses = register_review_lapse(card, review, previous_state, rating)
    session.add(review)
    if card.deck.bury_siblings:
        await bury_sibling_cards(session, card, timezone_name or user.timezone)
    await increment_daily_counter(session, card, previous_state, timezone_name or user.timezone)
    await track(session, user.id, "review_answer", rating=rating, state_after=card.state)
    if leech_alert_lapses is not None:
        await track(
            session,
            user.id,
            "leech_detected",
            review_lapses=leech_alert_lapses,
        )
    return review


async def _get_review_by_request_id(
    session: AsyncSession,
    user_id: int,
    request_id: str,
) -> ReviewLog | None:
    result = await session.execute(
        select(ReviewLog).where(
            ReviewLog.user_id == user_id,
            ReviewLog.request_id == request_id,
        )
    )
    return result.scalar_one_or_none()


def _replay_review(review: ReviewLog, card_id: int, rating: int) -> StudyAnswerResult:
    if review.card_id != card_id or review.rating != rating:
        raise AnswerRequestConflictError("Request ID was already used for another answer")
    if review.state_after is None:
        raise RuntimeError("Idempotent review is missing its saved state")
    return StudyAnswerResult(
        state=review.state_after,
        due_at=review.next_due_at,
        replayed=True,
        leech_alert_lapses=review.leech_alert_lapses,
    )


async def count_done_today(
    session: AsyncSession,
    user: User,
    now_utc: datetime | None = None,
) -> int:
    today = user_local_date(now_utc, user.timezone) if now_utc is not None else None
    result = await session.execute(
        select(func.count(ReviewLog.id)).where(
            ReviewLog.user_id == user.id,
            ReviewLog.reviewed_at >= user_day_start_utc(user.timezone, today),
        )
    )
    return int(result.scalar_one())
