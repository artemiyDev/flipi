import asyncio
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select

from bot.models import Card, Note, User
from bot.services.cards import create_note_with_cards, import_anki_note
from bot.services.decks import create_deck


async def _create_user(session, telegram_id: int = 1) -> User:
    user = User(telegram_id=telegram_id, timezone="UTC")
    session.add(user)
    await session.commit()
    await session.refresh(user)
    return user


def _card_spec(
    template_ord: int = 0,
    template_name: str = "Card 1",
    question_template: str = "{{Front}}",
    answer_template: str = "{{Back}}",
) -> dict:
    return {
        "direction": "front_back",
        "template_ord": template_ord,
        "template_name": template_name,
        "question_template": question_template,
        "answer_template": answer_template,
    }


async def _import_note(session, user, deck, **changes):
    values = {
        "front": "Question",
        "back": "Answer",
        "extra": "Details",
        "tags": ["tag"],
        "note_type": "Basic",
        "anki_guid": "anki-guid-1",
        "anki_model_id": "100",
        "fields": {"Front": "Question", "Back": "Answer", "Extra": "Details"},
        "source": "apkg",
        "card_specs": [_card_spec()],
    }
    values.update(changes)
    return await import_anki_note(session, user, deck, **values)


def test_repeated_apkg_import_is_unchanged(session_factory) -> None:
    async def verify() -> None:
        async with session_factory() as session:
            user = await _create_user(session)
            deck = await create_deck(session, user, "Imported")

            first = await _import_note(session, user, deck)
            second = await _import_note(session, user, deck)

            assert first.status == "added"
            assert first.added_cards == 1
            assert second.status == "unchanged"
            assert second.added_cards == 0
            assert (await session.execute(select(func.count(Note.id)))).scalar_one() == 1
            assert (await session.execute(select(func.count(Card.id)))).scalar_one() == 1

    asyncio.run(verify())


def test_apkg_merge_updates_note_without_changing_card_schedule(session_factory) -> None:
    async def verify() -> None:
        async with session_factory() as session:
            user = await _create_user(session)
            deck = await create_deck(session, user, "Imported")
            await _import_note(session, user, deck)
            card = (await session.execute(select(Card))).scalar_one()
            card.due_at = datetime.now(UTC) + timedelta(days=30)
            card.state = "review"
            card.fsrs_data = {"stability": 12.5}
            card.review_lapses = 4
            card.suspended = True
            card.leech_suspended_lapses = 4
            await session.commit()
            await session.refresh(card)
            due_at = card.due_at
            state = card.state
            fsrs_data = card.fsrs_data

            result = await _import_note(
                session,
                user,
                deck,
                front="Updated question",
                back="Updated answer",
                tags=["updated", "tag"],
                fields={
                    "Front": "Updated question",
                    "Back": "Updated answer",
                    "Extra": "Updated details",
                },
                extra="Updated details",
                card_specs=[_card_spec(template_name="Updated", question_template="{{Back}}", answer_template="{{Front}}")],
            )
            note = (await session.execute(select(Note))).scalar_one()
            await session.refresh(card)

            assert result.status == "updated"
            assert note.front == "Updated question"
            assert note.back == "Updated answer"
            assert note.tags == ["updated", "tag"]
            assert note.fields == {
                "Front": "Updated question",
                "Back": "Updated answer",
                "Extra": "Updated details",
            }
            assert card.due_at == due_at
            assert card.state == state
            assert card.fsrs_data == fsrs_data
            assert card.review_lapses == 4
            assert card.suspended is True
            assert card.leech_suspended_lapses == 4
            assert card.template_name == "Updated"
            assert card.question_template == "{{Back}}"
            assert card.answer_template == "{{Front}}"

    asyncio.run(verify())


def test_apkg_merge_adds_only_new_template_card(session_factory) -> None:
    async def verify() -> None:
        async with session_factory() as session:
            user = await _create_user(session)
            deck = await create_deck(session, user, "Imported")
            await _import_note(session, user, deck)
            existing_card = (await session.execute(select(Card))).scalar_one()
            existing_card.due_at = datetime.now(UTC) + timedelta(days=20)
            existing_card.state = "review"
            existing_card.fsrs_data = {"stability": 9.0}
            await session.commit()
            await session.refresh(existing_card)
            due_at = existing_card.due_at
            state = existing_card.state
            fsrs_data = existing_card.fsrs_data

            result = await _import_note(
                session,
                user,
                deck,
                card_specs=[_card_spec(), _card_spec(1, "Card 2", "{{Back}}", "{{Front}}")],
            )
            cards = list((await session.execute(select(Card).order_by(Card.template_ord))).scalars())

            assert result.status == "updated"
            assert result.added_cards == 1
            assert len(cards) == 2
            assert cards[0].due_at == due_at
            assert cards[0].state == state
            assert cards[0].fsrs_data == fsrs_data
            assert cards[1].template_ord == 1
            assert cards[1].state == "new"

    asyncio.run(verify())


def test_apkg_merge_assigns_guid_to_legacy_note(session_factory) -> None:
    async def verify() -> None:
        async with session_factory() as session:
            user = await _create_user(session)
            deck = await create_deck(session, user, "Imported")
            await create_note_with_cards(
                session=session,
                user=user,
                deck=deck,
                front="Question",
                back="Answer",
                tags=["tag"],
                note_type="Basic",
                anki_model_id="100",
                fields={"Front": "Question", "Back": "Answer", "Extra": "Details"},
                source="apkg",
                card_specs=[_card_spec()],
            )

            result = await _import_note(session, user, deck)
            note = (await session.execute(select(Note))).scalar_one()

            assert result.status == "updated"
            assert note.anki_guid == "anki-guid-1"
            assert (await session.execute(select(func.count(Note.id)))).scalar_one() == 1

    asyncio.run(verify())


def test_apkg_merge_matches_guid_across_decks(session_factory) -> None:
    async def verify() -> None:
        async with session_factory() as session:
            user = await _create_user(session)
            source_deck = await create_deck(session, user, "Source")
            target_deck = await create_deck(session, user, "Target")
            await _import_note(session, user, source_deck)

            result = await _import_note(session, user, target_deck, tags=["moved"])
            note = (await session.execute(select(Note))).scalar_one()

            assert result.status == "updated"
            assert note.deck_id == source_deck.id
            assert note.tags == ["moved"]
            assert (await session.execute(select(func.count(Note.id)))).scalar_one() == 1

    asyncio.run(verify())
