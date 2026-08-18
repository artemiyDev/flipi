from datetime import date
from pathlib import Path
import sqlite3
from typing import Annotated, Literal
import zipfile

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Response, UploadFile, status
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from sqlalchemy import select, text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app.deps import get_current_user, get_db_session
from bot.models import NoteStyle, User
from bot.services.apkg_importer import ImportedCard, parse_apkg_media, parse_apkg_notes
from bot.services.cards import (
    bury_card_until_tomorrow,
    card_answer_html,
    card_question,
    card_question_html,
    create_basic_note,
    delete_note,
    get_card,
    get_next_due_card,
    get_note,
    reset_card,
    search_cards_page,
    set_card_due_date,
    set_card_flag,
    set_card_suspended,
    update_note,
)
from bot.services.catalog import (
    CatalogDeckAlreadyInstalledError,
    install_catalog_deck,
    list_catalog_decks,
)
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
from bot.services.events import track
from bot.services.media import (
    extract_media_references,
    get_media_file,
    get_media_files_by_names,
    replace_image_media_references,
)
from bot.services.import_flow import ImportFlowError, import_apkg_notes, import_text_cards
from bot.services.importers import decode_text_payload, parse_text_cards
from bot.services.scheduler import preview_intervals
from bot.services.stats import forecast_due_counts, heatmap_review_counts, stats_overview
from bot.services.study import (
    answer_card,
    count_done_today,
    get_next_card_for_user,
    sanitize_card_css,
    sanitize_card_html,
)

router = APIRouter()
MAX_IMPORT_BYTES = 20 * 1024 * 1024


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


class CardCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    deck_id: int
    front: str
    back: str
    tags: list[str] | None = None
    reverse: bool = False

    @field_validator("front", "back")
    @classmethod
    def validate_content(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("Card content must not be empty")
        return value


class NotePatchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    front: str | None = None
    back: str | None = None
    fields: dict[str, str] | None = None
    tags: list[str] | None = None

    @model_validator(mode="after")
    def require_update(self):
        if not self.model_fields_set:
            raise ValueError("At least one note field is required")
        if self.front is not None and not self.front.strip():
            raise ValueError("Front must not be empty")
        if self.back is not None and not self.back.strip():
            raise ValueError("Back must not be empty")
        return self


class CardSuspendedRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    value: bool


class CardFlagRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    color: Literal["red", "orange", "green", "blue", "purple"] | None


class CardDueRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    date: date


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


async def card_response(session: AsyncSession, user: User, card) -> dict:
    decks_by_id = {deck.id: deck for deck in await list_all_user_decks(session, user)}
    question_html = card_question_html(card)
    answer_html = card_answer_html(card)
    media_files = await get_media_files_by_names(
        session, user, extract_media_references(question_html, answer_html)
    )
    return {
        "card_id": card.id,
        "note_id": card.note_id,
        "deck_id": card.deck_id,
        "deck_name": deck_full_path(card.deck, decks_by_id),
        "question_html": sanitize_card_html(
            replace_image_media_references(question_html, media_files)
        ),
        "answer_html": sanitize_card_html(
            replace_image_media_references(answer_html, media_files)
        ),
        "card_css": await get_card_css(session, user, card.note.anki_model_id),
        "media": [
            {
                "id": media.id,
                "name": media.original_name,
                "content_type": media.content_type,
            }
            for media in media_files
        ],
        "fields": card.note.fields or {},
        "front": card.note.front,
        "back": card.note.back,
        "tags": card.note.tags,
        "state": card.state,
        "due": card.due_at.isoformat(),
        "lapses": card.lapses,
        "suspended": card.suspended,
        "buried_until": card.buried_until.isoformat() if card.buried_until else None,
        "flag": card.flag,
        "template_ord": card.template_ord,
    }


async def get_card_css(
    session: AsyncSession,
    user: User,
    anki_model_id: str | None,
) -> str | None:
    if anki_model_id is None:
        return None
    result = await session.execute(
        select(NoteStyle.css).where(
            NoteStyle.user_id == user.id,
            NoteStyle.anki_model_id == anki_model_id,
        )
    )
    css = result.scalar_one_or_none()
    return sanitize_card_css(css) if css is not None else None


async def api_card_or_404(session: AsyncSession, user: User, card_id: int):
    card = await get_card(session, user, card_id)
    if card is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Card not found")
    return card


async def api_note_or_404(session: AsyncSession, user: User, note_id: int):
    note = await get_note(session, user, note_id)
    if note is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Note not found")
    return note


@router.post("/import")
async def import_file_endpoint(
    file: Annotated[UploadFile, File()],
    deck_id: Annotated[str, Form()],
    session: Annotated[AsyncSession, Depends(get_db_session)],
    user: Annotated[User, Depends(get_current_user)],
) -> dict:
    suffix = Path(file.filename or "").suffix.lower()
    if suffix not in {".apkg", ".csv", ".tsv", ".txt"}:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Поддерживаются файлы APKG, CSV, TSV и TXT.",
        )

    payload = await file.read(MAX_IMPORT_BYTES + 1)
    if len(payload) > MAX_IMPORT_BYTES:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail="Файл слишком большой. Текущий лимит импорта: 20 MB.",
        )

    if deck_id == "auto":
        if suffix != ".apkg":
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="Автоматическое создание колод доступно только для APKG.",
            )
        target_deck_id = None
    else:
        try:
            target_deck_id = int(deck_id)
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="deck_id должен быть целым числом или auto.",
            ) from exc

    try:
        if suffix == ".apkg":
            result = await import_apkg_notes(
                session,
                user,
                target_deck_id,
                parse_apkg_notes(payload),
                parse_apkg_media(payload),
                source="apkg",
            )
        else:
            rows = [
                ImportedCard(front=front, back=back, tags=tags, create_reverse=create_reverse)
                for front, back, tags, create_reverse in parse_text_cards(decode_text_payload(payload))
            ]
            result = await import_text_cards(session, user, target_deck_id, rows, source="import")
    except LookupError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except ImportFlowError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc
    except (sqlite3.Error, UnicodeDecodeError, ValueError, OSError, zipfile.BadZipFile) as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Не удалось прочитать файл: {exc}",
        ) from exc

    await track(
        session,
        user.id,
        "import_done",
        format=suffix.removeprefix("."),
        added=result.added,
        updated=result.updated,
        unchanged=result.unchanged,
    )
    await session.commit()
    return {
        "added": result.added,
        "updated": result.updated,
        "unchanged": result.unchanged,
        "decks_created": result.decks_created,
        "media_saved": result.media_saved,
    }


@router.post("/cards", status_code=status.HTTP_201_CREATED)
async def create_card_endpoint(
    payload: CardCreateRequest,
    session: Annotated[AsyncSession, Depends(get_db_session)],
    user: Annotated[User, Depends(get_current_user)],
) -> dict:
    deck = await get_deck(session, user, payload.deck_id)
    if deck is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Deck not found")
    note = await create_basic_note(
        session,
        user,
        deck,
        payload.front,
        payload.back,
        tags=payload.tags,
        create_reverse=payload.reverse,
        commit=False,
    )
    await track(session, user.id, "card_created", reverse=payload.reverse)
    await session.commit()
    return {"note_id": note.id}


@router.get("/cards/search")
async def search_cards_endpoint(
    session: Annotated[AsyncSession, Depends(get_db_session)],
    user: Annotated[User, Depends(get_current_user)],
    q: str = "",
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> dict:
    total, cards = await search_cards_page(session, user, q, limit=limit, offset=offset)
    decks_by_id = {deck.id: deck for deck in await list_all_user_decks(session, user)}
    return {
        "total": total,
        "items": [
            {
                "card_id": card.id,
                "note_id": card.note_id,
                "deck_id": card.deck_id,
                "deck_name": deck_full_path(card.deck, decks_by_id),
                "preview": card_question(card)[:200],
                "state": card.state,
                "due": card.due_at.isoformat(),
                "suspended": card.suspended,
                "buried": card.buried_until is not None,
                "flag": card.flag,
            }
            for card in cards
        ],
    }


@router.get("/cards/{card_id}")
async def card_details_endpoint(
    card_id: int,
    session: Annotated[AsyncSession, Depends(get_db_session)],
    user: Annotated[User, Depends(get_current_user)],
) -> dict:
    return await card_response(session, user, await api_card_or_404(session, user, card_id))


@router.patch("/notes/{note_id}")
async def update_note_endpoint(
    note_id: int,
    payload: NotePatchRequest,
    session: Annotated[AsyncSession, Depends(get_db_session)],
    user: Annotated[User, Depends(get_current_user)],
) -> dict:
    note = await api_note_or_404(session, user, note_id)
    values = payload.model_dump(exclude_unset=True)
    await update_note(session, note, **values)
    return {"ok": True}


@router.delete("/notes/{note_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_note_endpoint(
    note_id: int,
    session: Annotated[AsyncSession, Depends(get_db_session)],
    user: Annotated[User, Depends(get_current_user)],
) -> Response:
    await delete_note(session, await api_note_or_404(session, user, note_id))
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/cards/{card_id}/suspend")
async def suspend_card_endpoint(
    card_id: int,
    payload: CardSuspendedRequest,
    session: Annotated[AsyncSession, Depends(get_db_session)],
    user: Annotated[User, Depends(get_current_user)],
) -> dict:
    card = await api_card_or_404(session, user, card_id)
    await set_card_suspended(session, card, payload.value)
    return {"ok": True}


@router.post("/cards/{card_id}/bury")
async def bury_card_endpoint(
    card_id: int,
    session: Annotated[AsyncSession, Depends(get_db_session)],
    user: Annotated[User, Depends(get_current_user)],
) -> dict:
    card = await api_card_or_404(session, user, card_id)
    await bury_card_until_tomorrow(session, card, user.timezone)
    return {"ok": True}


@router.post("/cards/{card_id}/flag")
async def flag_card_endpoint(
    card_id: int,
    payload: CardFlagRequest,
    session: Annotated[AsyncSession, Depends(get_db_session)],
    user: Annotated[User, Depends(get_current_user)],
) -> dict:
    card = await api_card_or_404(session, user, card_id)
    await set_card_flag(session, card, payload.color)
    return {"ok": True}


@router.post("/cards/{card_id}/reset")
async def reset_card_endpoint(
    card_id: int,
    session: Annotated[AsyncSession, Depends(get_db_session)],
    user: Annotated[User, Depends(get_current_user)],
) -> dict:
    card = await api_card_or_404(session, user, card_id)
    await reset_card(session, card)
    return {"ok": True}


@router.post("/cards/{card_id}/due")
async def set_card_due_endpoint(
    card_id: int,
    payload: CardDueRequest,
    session: Annotated[AsyncSession, Depends(get_db_session)],
    user: Annotated[User, Depends(get_current_user)],
) -> dict:
    card = await api_card_or_404(session, user, card_id)
    await set_card_due_date(session, card, payload.date)
    return {"ok": True}


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


@router.get("/catalog")
async def catalog(
    session: Annotated[AsyncSession, Depends(get_db_session)],
    user: Annotated[User, Depends(get_current_user)],
) -> list[dict]:
    return await list_catalog_decks(session, user)


@router.post("/catalog/{slug}/install")
async def install_catalog(
    slug: str,
    session: Annotated[AsyncSession, Depends(get_db_session)],
    user: Annotated[User, Depends(get_current_user)],
) -> dict:
    try:
        result = await install_catalog_deck(session, user, slug)
    except CatalogDeckAlreadyInstalledError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    if result is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Catalog deck not found")
    return {"deck_id": result.deck_id, "added": result.added}


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
    await track(session, user.id, "deck_created")
    await session.commit()
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
        "card_css": await get_card_css(session, user, card.note.anki_model_id),
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
