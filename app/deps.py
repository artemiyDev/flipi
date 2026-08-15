from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import Depends, Header, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import InitDataValidationError, validate_init_data
from bot.config import get_settings
from bot.db import async_session
from bot.models import User
from bot.services.users import get_or_create_user


async def get_db_session() -> AsyncIterator[AsyncSession]:
    async with async_session() as session:
        yield session


async def get_current_user(
    session: Annotated[AsyncSession, Depends(get_db_session)],
    init_data: Annotated[str | None, Header(alias="X-Telegram-Init-Data")] = None,
) -> User:
    if init_data is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Telegram authorization is required",
        )

    settings = get_settings()
    try:
        telegram_user = validate_init_data(
            init_data,
            settings.bot_token,
            settings.auth_max_age_seconds,
        )
    except InitDataValidationError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid Telegram authorization",
        ) from exc
    return await get_or_create_user(session, telegram_user)
