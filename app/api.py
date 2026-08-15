from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app.deps import get_current_user, get_db_session
from bot.models import User
from bot.services.decks import deck_list_with_counts

router = APIRouter()


@router.get("/healthz")
async def healthz(session: Annotated[AsyncSession, Depends(get_db_session)]) -> dict:
    try:
        await session.execute(text("SELECT 1"))
    except SQLAlchemyError:
        return {"status": "ok", "database": False}
    return {"status": "ok", "database": True}


@router.get("/me")
async def me(user: Annotated[User, Depends(get_current_user)]) -> dict:
    return {
        "id": user.id,
        "telegram_id": user.telegram_id,
        "timezone": user.timezone,
    }


@router.get("/decks")
async def decks(
    session: Annotated[AsyncSession, Depends(get_db_session)],
    user: Annotated[User, Depends(get_current_user)],
) -> list[dict]:
    rows = await deck_list_with_counts(session, user)
    return [
        {
            "id": deck_id,
            "name": name,
            "new_count": new_count,
            "learning_count": learning_count,
            "review_count": review_count,
        }
        for deck_id, name, new_count, learning_count, review_count in rows
    ]
