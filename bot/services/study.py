import re
from datetime import datetime

import nh3
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from bot.models import Card, ReviewLog, User
from bot.services.cards import (
    bury_sibling_cards,
    get_next_due_card,
    increment_daily_counter,
)
from bot.services.decks import list_user_decks
from bot.services.events import track
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
    previous_state = card.state
    review = review_with_fsrs(card, card.deck, rating, elapsed_ms)
    session.add(review)
    if card.deck.bury_siblings:
        await bury_sibling_cards(session, card, user.timezone)
    await increment_daily_counter(session, card, previous_state, user.timezone)
    await track(session, user.id, "review_answer", rating=rating, state_after=card.state)
    await session.commit()
    return card


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
