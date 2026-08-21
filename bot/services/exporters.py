import csv
import base64
import json
from io import StringIO

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from bot.models import Card, Deck, MediaFile, ReviewLog, User
from bot.services.cards import card_answer, card_question


async def export_deck_csv(session: AsyncSession, deck: Deck) -> bytes:
    result = await session.execute(
        select(Card)
        .where(Card.deck_id == deck.id)
        .options(selectinload(Card.note), selectinload(Card.deck))
        .order_by(Card.id.asc())
    )
    cards = list(result.scalars())

    buffer = StringIO()
    writer = csv.writer(buffer)
    writer.writerow(
        [
            "card_id",
            "note_id",
            "deck",
            "question",
            "answer",
            "tags",
            "state",
            "due_at",
            "suspended",
            "buried_until",
            "flag",
            "reps",
            "lapses",
            "review_lapses",
        ]
    )
    for card in cards:
        writer.writerow(
            [
                card.id,
                card.note_id,
                deck.name,
                card_question(card),
                card_answer(card),
                " ".join(card.note.tags or []),
                card.state,
                card.due_at.isoformat(),
                card.suspended,
                card.buried_until.isoformat() if card.buried_until else "",
                card.flag or "",
                card.reps,
                card.lapses,
                card.review_lapses,
            ]
        )
    return buffer.getvalue().encode("utf-8-sig")


async def export_user_backup_json(session: AsyncSession, user: User) -> bytes:
    decks_result = await session.execute(
        select(Deck).where(Deck.user_id == user.id).order_by(Deck.id.asc())
    )
    decks = list(decks_result.scalars())

    backup = {
        "version": 1,
        "user": {
            "telegram_id": user.telegram_id,
            "username": user.username,
            "full_name": user.full_name,
            "timezone": user.timezone,
        },
        "decks": [],
        "media_files": [],
    }

    for deck in decks:
        cards_result = await session.execute(
            select(Card)
            .where(Card.deck_id == deck.id)
            .options(selectinload(Card.note), selectinload(Card.deck))
            .order_by(Card.note_id.asc(), Card.id.asc())
        )
        cards = list(cards_result.scalars())
        card_ids = [card.id for card in cards]
        review_logs: dict[int, list[dict]] = {card_id: [] for card_id in card_ids}
        if card_ids:
            reviews_result = await session.execute(
                select(ReviewLog)
                .where(ReviewLog.card_id.in_(card_ids))
                .order_by(ReviewLog.reviewed_at.asc(), ReviewLog.id.asc())
            )
            for review in reviews_result.scalars():
                review_logs.setdefault(review.card_id, []).append(
                    {
                        "rating": review.rating,
                        "reviewed_at": review.reviewed_at.isoformat(),
                        "elapsed_ms": review.elapsed_ms,
                        "previous_due_at": review.previous_due_at.isoformat()
                        if review.previous_due_at
                        else None,
                        "next_due_at": review.next_due_at.isoformat(),
                        "fsrs_review_log": review.fsrs_review_log,
                        "leech_alert_lapses": review.leech_alert_lapses,
                    }
                )

        notes: dict[int, dict] = {}
        for card in cards:
            note_payload = notes.setdefault(
                card.note_id,
                {
                    "id": card.note_id,
                    "note_type": card.note.note_type,
                    "anki_model_id": card.note.anki_model_id,
                    "fields": card.note.fields,
                    "front": card.note.front,
                    "back": card.note.back,
                    "extra": card.note.extra,
                    "tags": card.note.tags or [],
                    "source": card.note.source,
                    "cards": [],
                },
            )
            note_payload["cards"].append(
                {
                    "id": card.id,
                    "direction": card.direction,
                    "template_name": card.template_name,
                    "template_ord": card.template_ord,
                    "question_template": card.question_template,
                    "answer_template": card.answer_template,
                    "due_at": card.due_at.isoformat(),
                    "state": card.state,
                    "fsrs_data": card.fsrs_data,
                    "suspended": card.suspended,
                    "buried_until": card.buried_until.isoformat() if card.buried_until else None,
                    "flag": card.flag,
                    "reps": card.reps,
                    "lapses": card.lapses,
                    "review_lapses": card.review_lapses,
                    "leech_suspended_lapses": card.leech_suspended_lapses,
                    "reviews": review_logs.get(card.id, []),
                }
            )

        backup["decks"].append(
            {
                "id": deck.id,
                "name": deck.name,
                "description": deck.description,
                "is_archived": deck.is_archived,
                "new_cards_per_day": deck.new_cards_per_day,
                "reviews_per_day": deck.reviews_per_day,
                "desired_retention": deck.desired_retention,
                "bury_siblings": deck.bury_siblings,
                "learning_steps_minutes": deck.learning_steps_minutes,
                "relearning_steps_minutes": deck.relearning_steps_minutes,
                "maximum_interval_days": deck.maximum_interval_days,
                "enable_fuzzing": deck.enable_fuzzing,
                "option_preset": deck.option_preset,
                "fsrs_parameters": deck.fsrs_parameters,
                "notes": list(notes.values()),
            }
        )

    media_result = await session.execute(
        select(MediaFile, Deck.name)
        .outerjoin(Deck, MediaFile.deck_id == Deck.id)
        .where(MediaFile.user_id == user.id)
        .order_by(MediaFile.id.asc())
    )
    for media, deck_name in media_result.all():
        backup["media_files"].append(
            {
                "original_name": media.original_name,
                "content_type": media.content_type,
                "size_bytes": media.size_bytes,
                "sha256": media.sha256,
                "deck_name": deck_name,
                "content_base64": base64.b64encode(media.content).decode("ascii"),
            }
        )

    return json.dumps(backup, ensure_ascii=False, indent=2).encode("utf-8")
