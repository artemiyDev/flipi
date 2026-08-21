import base64
import json
from datetime import UTC, date, datetime
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from bot.models import Card, Note, ReviewLog, User
from bot.services.apkg_importer import ImportedMedia
from bot.services.cards import note_exists
from bot.services.decks import get_or_create_deck
from bot.services.leeches import is_leech_alert_count
from bot.services.media import save_imported_media_files
from bot.services.scheduler import new_fsrs_card_json
from bot.services.timezones import normalize_timezone


async def restore_user_backup_json(
    session: AsyncSession,
    user: User,
    payload: bytes,
) -> dict[str, int]:
    backup = json.loads(payload.decode("utf-8-sig"))
    if backup.get("version") != 1 or not isinstance(backup.get("decks"), list):
        raise ValueError("Unsupported backup format.")

    backup_timezone = (backup.get("user") or {}).get("timezone")
    if backup_timezone:
        try:
            user.timezone = normalize_timezone(str(backup_timezone))
        except Exception:
            pass

    stats = {
        "decks": 0,
        "notes": 0,
        "cards": 0,
        "reviews": 0,
        "media": 0,
        "skipped_media": 0,
        "skipped_notes": 0,
    }
    deck_cache = {}

    for deck_payload in backup["decks"]:
        deck = await get_or_create_deck(
            session,
            user,
            str(deck_payload.get("name") or "Restored Deck"),
            deck_payload.get("description"),
        )
        deck.description = deck_payload.get("description")
        deck.is_archived = bool(deck_payload.get("is_archived", False))
        deck.new_cards_per_day = int(deck_payload.get("new_cards_per_day", deck.new_cards_per_day))
        deck.reviews_per_day = int(deck_payload.get("reviews_per_day", deck.reviews_per_day))
        deck.desired_retention = float(deck_payload.get("desired_retention", deck.desired_retention))
        deck.bury_siblings = bool(deck_payload.get("bury_siblings", deck.bury_siblings))
        deck.learning_steps_minutes = list(
            deck_payload.get("learning_steps_minutes") or deck.learning_steps_minutes
        )
        deck.relearning_steps_minutes = list(
            deck_payload.get("relearning_steps_minutes") or deck.relearning_steps_minutes
        )
        deck.maximum_interval_days = int(
            deck_payload.get("maximum_interval_days", deck.maximum_interval_days)
        )
        deck.enable_fuzzing = bool(deck_payload.get("enable_fuzzing", deck.enable_fuzzing))
        deck.option_preset = str(deck_payload.get("option_preset") or deck.option_preset)
        deck.fsrs_parameters = deck_payload.get("fsrs_parameters")
        deck_cache[deck.name] = deck
        stats["decks"] += 1

        for note_payload in deck_payload.get("notes", []):
            front = str(note_payload.get("front") or "").strip()
            back = str(note_payload.get("back") or "").strip()
            if not front or not back:
                continue
            if await note_exists(session, user, deck, front, back):
                stats["skipped_notes"] += 1
                continue

            note = Note(
                user_id=user.id,
                deck_id=deck.id,
                note_type=str(note_payload.get("note_type") or "basic"),
                anki_model_id=note_payload.get("anki_model_id"),
                fields=note_payload.get("fields"),
                front=front,
                back=back,
                extra=note_payload.get("extra"),
                tags=list(note_payload.get("tags") or []),
                source=note_payload.get("source") or "restore",
            )
            session.add(note)
            await session.flush()
            stats["notes"] += 1

            cards_payload = note_payload.get("cards") or [
                {"direction": "front_back", "state": "new", "fsrs_data": new_fsrs_card_json()}
            ]
            for card_payload in cards_payload:
                suspended = bool(card_payload.get("suspended", False))
                review_lapses = int(card_payload.get("review_lapses", 0) or 0)
                leech_suspended_lapses = _parse_optional_int(
                    card_payload.get("leech_suspended_lapses")
                )
                if (
                    not suspended
                    or leech_suspended_lapses != review_lapses
                    or not is_leech_alert_count(review_lapses)
                ):
                    leech_suspended_lapses = None
                card = Card(
                    user_id=user.id,
                    deck_id=deck.id,
                    note_id=note.id,
                    direction=str(card_payload.get("direction") or "front_back"),
                    template_name=card_payload.get("template_name"),
                    template_ord=int(card_payload.get("template_ord", 0)),
                    question_template=card_payload.get("question_template"),
                    answer_template=card_payload.get("answer_template"),
                    due_at=_parse_datetime(card_payload.get("due_at")),
                    state=str(card_payload.get("state") or "new"),
                    fsrs_data=card_payload.get("fsrs_data") or new_fsrs_card_json(),
                    suspended=suspended,
                    buried_until=_parse_date(card_payload.get("buried_until")),
                    flag=card_payload.get("flag"),
                    reps=int(card_payload.get("reps", 0)),
                    lapses=int(card_payload.get("lapses", 0)),
                    review_lapses=review_lapses,
                    leech_suspended_lapses=leech_suspended_lapses,
                )
                session.add(card)
                await session.flush()
                stats["cards"] += 1

                for review_payload in card_payload.get("reviews", []):
                    session.add(
                        ReviewLog(
                            user_id=user.id,
                            deck_id=deck.id,
                            card_id=card.id,
                            rating=int(review_payload.get("rating", 0)),
                            reviewed_at=_parse_datetime(review_payload.get("reviewed_at")),
                            elapsed_ms=review_payload.get("elapsed_ms"),
                            previous_due_at=_parse_optional_datetime(
                                review_payload.get("previous_due_at")
                            ),
                            next_due_at=_parse_datetime(review_payload.get("next_due_at")),
                            fsrs_review_log=review_payload.get("fsrs_review_log"),
                            leech_alert_lapses=_parse_optional_int(
                                review_payload.get("leech_alert_lapses")
                            ),
                        )
                    )
                    stats["reviews"] += 1

    for media_payload in backup.get("media_files", []):
        content_base64 = media_payload.get("content_base64")
        original_name = media_payload.get("original_name")
        sha256 = media_payload.get("sha256")
        if not content_base64 or not original_name or not sha256:
            continue
        deck = None
        deck_name = media_payload.get("deck_name")
        if deck_name:
            deck = deck_cache.get(deck_name)
            if deck is None:
                deck = await get_or_create_deck(session, user, str(deck_name))
                deck_cache[str(deck_name)] = deck
        saved, skipped = await save_imported_media_files(
            session,
            user,
            deck,
            [
                ImportedMedia(
                    original_name=str(original_name),
                    content=base64.b64decode(str(content_base64)),
                    sha256=str(sha256),
                )
            ],
        )
        stats["media"] += saved
        stats["skipped_media"] += skipped

    await session.commit()
    return stats


def _parse_datetime(value: Any) -> datetime:
    if not value:
        return datetime.now(UTC)
    parsed = datetime.fromisoformat(str(value))
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed


def _parse_optional_datetime(value: Any) -> datetime | None:
    if not value:
        return None
    return _parse_datetime(value)


def _parse_date(value: Any) -> date | None:
    if not value:
        return None
    return date.fromisoformat(str(value))


def _parse_optional_int(value: Any) -> int | None:
    if value is None:
        return None
    return int(value)
