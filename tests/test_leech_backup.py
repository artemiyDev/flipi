import asyncio
import csv
import json
from datetime import UTC, datetime, timedelta
from io import StringIO
from types import SimpleNamespace

from sqlalchemy import select

from bot.models import Card, ReviewLog
from bot.services.backups import restore_user_backup_json
from bot.services.cards import create_basic_note
from bot.services.decks import create_deck
from bot.services.exporters import export_deck_csv, export_user_backup_json
from bot.services.users import get_or_create_user


def _telegram_user(telegram_id: int) -> SimpleNamespace:
    return SimpleNamespace(
        id=telegram_id,
        username=f"user-{telegram_id}",
        full_name=f"User {telegram_id}",
        language_code="en",
    )


def test_csv_and_json_round_trip_preserve_leech_state(session_factory) -> None:
    async def check() -> None:
        async with session_factory() as session:
            source_user = await get_or_create_user(session, _telegram_user(8101))
            deck = await create_deck(session, source_user, "Backup leeches")
            note = await create_basic_note(session, source_user, deck, "front", "back")
            card = (await session.scalars(select(Card).where(Card.note_id == note.id))).one()
            card.state = "relearning"
            card.review_lapses = 4
            card.suspended = True
            card.leech_suspended_lapses = 4
            now = datetime.now(UTC)
            session.add(
                ReviewLog(
                    user_id=source_user.id,
                    deck_id=deck.id,
                    card_id=card.id,
                    rating=1,
                    reviewed_at=now,
                    elapsed_ms=150,
                    previous_due_at=now - timedelta(days=1),
                    next_due_at=now + timedelta(minutes=10),
                    fsrs_review_log={},
                    leech_alert_lapses=4,
                )
            )
            await session.commit()

            csv_payload = await export_deck_csv(session, deck)
            backup_payload = await export_user_backup_json(session, source_user)

        csv_rows = list(csv.DictReader(StringIO(csv_payload.decode("utf-8-sig"))))
        assert csv_rows[0]["review_lapses"] == "4"

        backup = json.loads(backup_payload)
        card_payload = backup["decks"][0]["notes"][0]["cards"][0]
        assert card_payload["review_lapses"] == 4
        assert card_payload["leech_suspended_lapses"] == 4
        assert card_payload["reviews"][0]["leech_alert_lapses"] == 4

        async with session_factory() as session:
            restored_user = await get_or_create_user(session, _telegram_user(8102))
            stats = await restore_user_backup_json(session, restored_user, backup_payload)
            assert stats["cards"] == 1
            restored_card = (
                await session.scalars(select(Card).where(Card.user_id == restored_user.id))
            ).one()
            restored_review = (
                await session.scalars(
                    select(ReviewLog).where(ReviewLog.user_id == restored_user.id)
                )
            ).one()
            assert restored_card.review_lapses == 4
            assert restored_card.suspended is True
            assert restored_card.leech_suspended_lapses == 4
            assert restored_review.leech_alert_lapses == 4

        old_backup = json.loads(backup_payload)
        old_card = old_backup["decks"][0]["notes"][0]["cards"][0]
        old_card.pop("review_lapses")
        old_card.pop("leech_suspended_lapses")
        old_card["reviews"][0].pop("leech_alert_lapses")

        async with session_factory() as session:
            old_user = await get_or_create_user(session, _telegram_user(8103))
            await restore_user_backup_json(
                session,
                old_user,
                json.dumps(old_backup).encode("utf-8"),
            )
            restored_card = (
                await session.scalars(select(Card).where(Card.user_id == old_user.id))
            ).one()
            restored_review = (
                await session.scalars(select(ReviewLog).where(ReviewLog.user_id == old_user.id))
            ).one()
            assert restored_card.review_lapses == 0
            assert restored_card.leech_suspended_lapses is None
            assert restored_review.leech_alert_lapses is None

        malformed_backup = json.loads(backup_payload)
        malformed_card = malformed_backup["decks"][0]["notes"][0]["cards"][0]
        malformed_card["suspended"] = False

        async with session_factory() as session:
            malformed_user = await get_or_create_user(session, _telegram_user(8104))
            await restore_user_backup_json(
                session,
                malformed_user,
                json.dumps(malformed_backup).encode("utf-8"),
            )
            restored_card = (
                await session.scalars(
                    select(Card).where(Card.user_id == malformed_user.id)
                )
            ).one()
            assert restored_card.review_lapses == 4
            assert restored_card.suspended is False
            assert restored_card.leech_suspended_lapses is None

    asyncio.run(check())
