from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app.deps import get_current_user, get_db_session
from bot.models import User
from bot.services.cards import card_answer_html, card_question_html, get_card, get_next_due_card
from bot.services.decks import (
    DECK_OPTION_PRESETS,
    DeckNameConflictError,
    apply_deck_preset,
    archive_deck,
    create_api_root_deck,
    deck_full_path,
    deck_list_with_counts,
    get_any_deck,
    get_deck,
    get_deck_counts,
    list_all_user_decks,
    list_archived_deck_display_choices,
    list_user_decks,
    rename_api_deck,
    restore_deck,
    update_deck_settings,
)
from bot.services.media import (
    extract_media_references,
    get_media_file,
    get_media_files_by_names,
    replace_image_media_references,
)
from bot.services.scheduler import preview_intervals
from bot.services.stats import forecast_due_counts, heatmap_review_counts, stats_overview
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


class DeckCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    description: str | None = None


class DeckRenameRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str


class DeckSettingsPatchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    new_cards_per_day: int | None = None
    reviews_per_day: int | None = None
    desired_retention: float | None = None
    learning_steps_minutes: list[int] | None = None
    relearning_steps_minutes: list[int] | None = None
    maximum_interval_days: int | None = None
    bury_siblings: bool | None = None
    enable_fuzzing: bool | None = None


class DeckPresetRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str


async def deck_detail(session: AsyncSession, user: User, deck) -> dict:
    decks_by_id = {item.id: item for item in await list_all_user_decks(session, user)}
    new_count, learning_count, review_count = await get_deck_counts(session, deck)
    return {
        "id": deck.id,
        "name": deck_full_path(deck, decks_by_id),
        "description": deck.description,
        "is_archived": deck.is_archived,
        "settings": {
            "new_cards_per_day": deck.new_cards_per_day,
            "reviews_per_day": deck.reviews_per_day,
            "desired_retention": deck.desired_retention,
            "learning_steps_minutes": deck.learning_steps_minutes,
            "relearning_steps_minutes": deck.relearning_steps_minutes,
            "maximum_interval_days": deck.maximum_interval_days,
            "bury_siblings": deck.bury_siblings,
            "enable_fuzzing": deck.enable_fuzzing,
            "option_preset": deck.option_preset,
        },
        "counts": {
            "new": new_count,
            "learning": learning_count,
            "review": review_count,
        },
    }


async def api_deck_or_404(session: AsyncSession, user: User, deck_id: int):
    deck = await get_any_deck(session, user, deck_id)
    if deck is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Deck not found")
    return deck


@router.get("/stats/overview")
async def stats_overview_endpoint(
    session: Annotated[AsyncSession, Depends(get_db_session)],
    user: Annotated[User, Depends(get_current_user)],
) -> dict:
    return await stats_overview(session, user)


@router.get("/stats/heatmap")
async def stats_heatmap(
    session: Annotated[AsyncSession, Depends(get_db_session)],
    user: Annotated[User, Depends(get_current_user)],
    weeks: Annotated[int, Query(ge=1, le=53)] = 26,
) -> dict:
    counts = await heatmap_review_counts(session, user, weeks)
    return {"days": [{"date": day.isoformat(), "count": count} for day, count in counts]}


@router.get("/stats/forecast")
async def stats_forecast(
    session: Annotated[AsyncSession, Depends(get_db_session)],
    user: Annotated[User, Depends(get_current_user)],
    days: Annotated[int, Query(ge=1, le=90)] = 30,
) -> dict:
    overdue, counts = await forecast_due_counts(session, user, days)
    return {
        "overdue": overdue,
        "days": [{"date": day.isoformat(), "count": count} for day, count in counts],
    }


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


@router.post("/decks", status_code=status.HTTP_201_CREATED)
async def create_deck_endpoint(
    payload: DeckCreateRequest,
    session: Annotated[AsyncSession, Depends(get_db_session)],
    user: Annotated[User, Depends(get_current_user)],
) -> dict:
    try:
        deck = await create_api_root_deck(session, user, payload.name, payload.description)
    except DeckNameConflictError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc
    return await deck_detail(session, user, deck)


@router.get("/decks/archived")
async def archived_decks(
    session: Annotated[AsyncSession, Depends(get_db_session)],
    user: Annotated[User, Depends(get_current_user)],
) -> list[dict]:
    return [
        {"id": deck_id, "name": name}
        for deck_id, name in await list_archived_deck_display_choices(session, user)
    ]


@router.get("/decks/presets")
async def deck_presets(user: Annotated[User, Depends(get_current_user)]) -> dict:
    return DECK_OPTION_PRESETS


@router.get("/decks/{deck_id}")
async def get_deck_endpoint(
    deck_id: int,
    session: Annotated[AsyncSession, Depends(get_db_session)],
    user: Annotated[User, Depends(get_current_user)],
) -> dict:
    deck = await api_deck_or_404(session, user, deck_id)
    return await deck_detail(session, user, deck)


@router.patch("/decks/{deck_id}")
async def rename_deck_endpoint(
    deck_id: int,
    payload: DeckRenameRequest,
    session: Annotated[AsyncSession, Depends(get_db_session)],
    user: Annotated[User, Depends(get_current_user)],
) -> dict:
    deck = await api_deck_or_404(session, user, deck_id)
    try:
        await rename_api_deck(session, user, deck, payload.name)
    except DeckNameConflictError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc
    return await deck_detail(session, user, deck)


@router.post("/decks/{deck_id}/archive")
async def archive_deck_endpoint(
    deck_id: int,
    session: Annotated[AsyncSession, Depends(get_db_session)],
    user: Annotated[User, Depends(get_current_user)],
) -> dict:
    deck = await api_deck_or_404(session, user, deck_id)
    await archive_deck(session, deck)
    return await deck_detail(session, user, deck)


@router.post("/decks/{deck_id}/restore")
async def restore_deck_endpoint(
    deck_id: int,
    session: Annotated[AsyncSession, Depends(get_db_session)],
    user: Annotated[User, Depends(get_current_user)],
) -> dict:
    deck = await api_deck_or_404(session, user, deck_id)
    await restore_deck(session, deck)
    return await deck_detail(session, user, deck)


@router.patch("/decks/{deck_id}/settings")
async def update_deck_settings_endpoint(
    deck_id: int,
    payload: DeckSettingsPatchRequest,
    session: Annotated[AsyncSession, Depends(get_db_session)],
    user: Annotated[User, Depends(get_current_user)],
) -> dict:
    deck = await api_deck_or_404(session, user, deck_id)
    try:
        await update_deck_settings(session, deck, payload.model_dump(exclude_unset=True))
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc
    return await deck_detail(session, user, deck)


@router.post("/decks/{deck_id}/preset")
async def apply_deck_preset_endpoint(
    deck_id: int,
    payload: DeckPresetRequest,
    session: Annotated[AsyncSession, Depends(get_db_session)],
    user: Annotated[User, Depends(get_current_user)],
) -> dict:
    deck = await api_deck_or_404(session, user, deck_id)
    try:
        await apply_deck_preset(session, deck, payload.name)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc
    return await deck_detail(session, user, deck)


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
