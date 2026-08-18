import logging

from sqlalchemy.ext.asyncio import AsyncSession

from bot.models import Event

logger = logging.getLogger(__name__)


async def track(session: AsyncSession, user_id: int | None, name: str, **props) -> None:
    try:
        session.add(Event(user_id=user_id, name=name, props=props or None))
    except Exception:
        logger.warning("Unable to track event %s", name, exc_info=True)
