from typing import Protocol
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from bot.models import User
from bot.services.timezones import normalize_timezone


class TelegramUser(Protocol):
    id: int
    username: str | None
    full_name: str | None
    language_code: str | None


async def get_or_create_user(session: AsyncSession, tg_user: TelegramUser) -> User:
    result = await session.execute(select(User).where(User.telegram_id == tg_user.id))
    user = result.scalar_one_or_none()
    if user is None:
        user = User(
            telegram_id=tg_user.id,
            username=tg_user.username,
            full_name=tg_user.full_name,
            language_code=tg_user.language_code,
        )
        session.add(user)
        await session.commit()
        await session.refresh(user)
        return user

    changed = False
    if user.username != tg_user.username:
        user.username = tg_user.username
        changed = True
    if user.full_name != tg_user.full_name:
        user.full_name = tg_user.full_name
        changed = True
    if user.language_code != tg_user.language_code:
        user.language_code = tg_user.language_code
        changed = True
    if changed:
        await session.commit()
    return user


async def update_user_timezone(session: AsyncSession, user: User, timezone_name: str) -> None:
    user.timezone = normalize_timezone(timezone_name)
    await session.commit()
