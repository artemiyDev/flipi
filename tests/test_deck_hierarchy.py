import asyncio

from sqlalchemy import func, select

from bot.handlers.import_cards import _get_cached_deck
from bot.models import Card, Deck, Note, User
from bot.services.cards import create_note_with_cards, note_exists
from bot.services.decks import create_deck, deck_full_path, get_or_create_deck, resolve_apkg_deck


async def _create_user(session, telegram_id: int = 1) -> User:
    user = User(telegram_id=telegram_id, timezone="UTC")
    session.add(user)
    await session.commit()
    await session.refresh(user)
    return user


def test_apkg_hierarchy_places_notes_and_cards_in_leaf(session_factory) -> None:
    async def verify() -> None:
        async with session_factory() as session:
            user = await _create_user(session)
            leaf = await _get_cached_deck(session, user, {}, "A::B::C")
            note = await create_note_with_cards(
                session=session,
                user=user,
                deck=leaf,
                front="Question",
                back="Answer",
                tags=[],
                note_type="basic",
                anki_model_id=None,
                fields=None,
                source="apkg",
                card_specs=[{"deck": leaf, "direction": "front_back"}],
            )

            decks = list(
                (await session.execute(select(Deck).order_by(Deck.id.asc()))).scalars()
            )
            card = (await session.execute(select(Card))).scalar_one()

            assert [deck.name for deck in decks] == ["A", "B", "C"]
            assert decks[0].parent_id is None
            assert decks[1].parent_id == decks[0].id
            assert decks[2].parent_id == decks[1].id
            assert note.deck_id == decks[2].id
            assert card.deck_id == decks[2].id

    asyncio.run(verify())


def test_same_leaf_name_is_scoped_to_parent(session_factory) -> None:
    async def verify() -> None:
        async with session_factory() as session:
            user = await _create_user(session)
            spanish_leaf = await resolve_apkg_deck(session, user, "Spanish::Vocabulary")
            french_leaf = await resolve_apkg_deck(session, user, "French::Vocabulary")

            spanish_note = await create_note_with_cards(
                session=session,
                user=user,
                deck=spanish_leaf,
                front="Hola",
                back="Hello",
                tags=[],
                note_type="basic",
                anki_model_id=None,
                fields=None,
                source="apkg",
                card_specs=[{"deck": spanish_leaf, "direction": "front_back"}],
            )
            french_note = await create_note_with_cards(
                session=session,
                user=user,
                deck=french_leaf,
                front="Bonjour",
                back="Hello",
                tags=[],
                note_type="basic",
                anki_model_id=None,
                fields=None,
                source="apkg",
                card_specs=[{"deck": french_leaf, "direction": "front_back"}],
            )

            assert spanish_leaf.id != french_leaf.id
            assert spanish_leaf.name == french_leaf.name == "Vocabulary"
            assert spanish_leaf.parent_id != french_leaf.parent_id
            assert spanish_note.deck_id == spanish_leaf.id
            assert french_note.deck_id == french_leaf.id

    asyncio.run(verify())


def test_repeat_import_reuses_hierarchy_and_skips_duplicate_note(session_factory) -> None:
    async def verify() -> None:
        async with session_factory() as session:
            user = await _create_user(session)
            first_leaf = await resolve_apkg_deck(session, user, "A::B::C")
            assert not await note_exists(session, user, first_leaf, "Question", "Answer")
            await create_note_with_cards(
                session=session,
                user=user,
                deck=first_leaf,
                front="Question",
                back="Answer",
                tags=[],
                note_type="basic",
                anki_model_id=None,
                fields=None,
                source="apkg",
                card_specs=[{"deck": first_leaf, "direction": "front_back"}],
            )

            repeated_leaf = await resolve_apkg_deck(session, user, "A::B::C")
            assert repeated_leaf.id == first_leaf.id
            assert await note_exists(session, user, repeated_leaf, "Question", "Answer")
            assert (await session.execute(select(func.count(Deck.id)))).scalar_one() == 3
            assert (await session.execute(select(func.count(Note.id)))).scalar_one() == 1

    asyncio.run(verify())


def test_existing_flat_deck_is_reused(session_factory) -> None:
    async def verify() -> None:
        async with session_factory() as session:
            user = await _create_user(session)
            flat_deck = await create_deck(session, user, "A::B::C")

            resolved = await resolve_apkg_deck(session, user, "A::B::C")

            assert resolved.id == flat_deck.id
            assert (await session.execute(select(func.count(Deck.id)))).scalar_one() == 1

    asyncio.run(verify())


def test_root_leaf_name_does_not_intercept_nested_import(session_factory) -> None:
    async def verify() -> None:
        async with session_factory() as session:
            user = await _create_user(session)
            root_vocabulary = await create_deck(session, user, "Vocabulary")

            nested_vocabulary = await resolve_apkg_deck(session, user, "Spanish::Vocabulary")

            assert nested_vocabulary.id != root_vocabulary.id
            assert nested_vocabulary.name == root_vocabulary.name == "Vocabulary"
            assert nested_vocabulary.parent_id is not None

    asyncio.run(verify())


def test_root_deck_lookup_ignores_nested_deck_with_same_name(session_factory) -> None:
    async def verify() -> None:
        async with session_factory() as session:
            user = await _create_user(session)
            nested_vocabulary = await resolve_apkg_deck(session, user, "Spanish::Vocabulary")

            root_vocabulary = await get_or_create_deck(session, user, "Vocabulary")

            assert root_vocabulary.id != nested_vocabulary.id
            assert root_vocabulary.parent_id is None

    asyncio.run(verify())


def test_only_leaf_receives_import_description(session_factory) -> None:
    async def verify() -> None:
        async with session_factory() as session:
            user = await _create_user(session)
            leaf = await resolve_apkg_deck(session, user, "A::B::C", "Imported from APKG")
            decks = list(
                (await session.execute(select(Deck).order_by(Deck.id.asc()))).scalars()
            )

            assert [deck.description for deck in decks] == [None, None, "Imported from APKG"]
            assert leaf.id == decks[-1].id

    asyncio.run(verify())


def test_deck_full_path_uses_parent_chain() -> None:
    root = Deck(id=1, user_id=1, name="A")
    middle = Deck(id=2, user_id=1, parent_id=root.id, name="B")
    leaf = Deck(id=3, user_id=1, parent_id=middle.id, name="C")

    assert deck_full_path(leaf, {root.id: root, middle.id: middle, leaf.id: leaf}) == "A::B::C"
