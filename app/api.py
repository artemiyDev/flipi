from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Response, status
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app.deps import get_current_user, get_db_session
from bot.models import User
from bot.services.cards import card_answer_html, card_question_html, get_card, get_next_due_card
from bot.services.decks import (
    deck_full_path,
    deck_list_with_counts,
    get_deck,
    get_deck_counts,
    list_user_decks,
)
from bot.services.media import (
    extract_media_references,
    get_media_file,
    get_media_files_by_names,
    replace_image_media_references,
)
from bot.services.scheduler import preview_intervals
from bot.services.study import (
    answer_card,
    count_done_today,
    get_next_card_for_user,
    sanitize_card_html,
)

router = APIRouter()


class StudyAnswerRequest(BaseModel):
    card_id: int
    rating: int = Field(ge=1, le=4)
    elapsed_ms: int | None = None


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


@router.get("/study/next")
async def study_next(
    session: Annotated[AsyncSession, Depends(get_db_session)],
    user: Annotated[User, Depends(get_current_user)],
    deck_id: str = "all",
) -> dict:
    if deck_id == "all":
        card = await get_next_card_for_user(session, user)
    else:
        try:
            parsed_deck_id = int(deck_id)
        except ValueError as exc:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY) from exc
        deck = await get_deck(session, user, parsed_deck_id)
        if deck is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Deck not found")
        card = await get_next_due_card(session, deck, user.timezone)

    if card is None:
        return {"card_id": None, "done_today": await count_done_today(session, user)}

    question_html = card_question_html(card)
    answer_html = card_answer_html(card)
    media_files = await get_media_files_by_names(
        session, user, extract_media_references(question_html, answer_html)
    )
    decks_by_id = {deck.id: deck for deck in await list_user_decks(session, user)}
    new_count, learning_count, review_count = await get_deck_counts(session, card.deck)
    return {
        "card_id": card.id,
        "deck_id": card.deck_id,
        "deck_name": deck_full_path(card.deck, decks_by_id),
        "progress": {
            "new": new_count,
            "learning": learning_count,
            "review": review_count,
        },
        "question_html": sanitize_card_html(
            replace_image_media_references(question_html, media_files)
        ),
        "answer_html": sanitize_card_html(
            replace_image_media_references(answer_html, media_files)
        ),
        "media": [
            {
                "id": media.id,
                "name": media.original_name,
                "content_type": media.content_type,
            }
            for media in media_files
        ],
        "intervals": preview_intervals(card, card.deck),
    }


@router.post("/study/answer")
async def study_answer(
    payload: StudyAnswerRequest,
    session: Annotated[AsyncSession, Depends(get_db_session)],
    user: Annotated[User, Depends(get_current_user)],
) -> dict:
    card = await get_card(session, user, payload.card_id)
    if card is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Card not found")
    await answer_card(session, user, card, payload.rating, payload.elapsed_ms)
    return {"ok": True, "state": card.state, "due": card.due_at.isoformat()}


@router.get("/media/{media_id}")
async def media(
    media_id: int,
    session: Annotated[AsyncSession, Depends(get_db_session)],
    user: Annotated[User, Depends(get_current_user)],
) -> Response:
    media_file = await get_media_file(session, user, media_id)
    if media_file is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Media not found")
    return Response(
        content=media_file.content,
        media_type=media_file.content_type or "application/octet-stream",
        headers={"Cache-Control": "private, max-age=86400"},
    )
