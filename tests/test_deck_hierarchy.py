import asyncio
from types import SimpleNamespace

from bot.handlers import import_cards
from bot.models import Deck, User
from bot.services import decks
from bot.services.apkg_importer import ImportedCard, ImportedNote


class _SessionContext:
    async def __aenter__(self):
        return SimpleNamespace()

    async def __aexit__(self, exc_type, exc_value, traceback):
        return None


class _State:
    async def get_data(self) -> dict[str, str]:
        return {"deck_id": "auto"}

    async def clear(self) -> None:
        return None


class _Message:
    from_user = SimpleNamespace(id=1)

    async def answer(self, text: str) -> None:
        return None


def _deck_resolver(monkeypatch):
    created: dict[str, Deck] = {}
    user = User(id=1, telegram_id=1, timezone="UTC")

    async def find_deck(session, found_user, name: str):
        return created.get(name)

    async def get_or_create(session, found_user, name: str, description=None, parent=None):
        deck = created.get(name)
        if deck is None:
            deck = Deck(
                id=len(created) + 1,
                user_id=found_user.id,
                parent_id=parent.id if parent is not None else None,
                name=name,
                description=description,
            )
            created[name] = deck
        return deck

    monkeypatch.setattr(decks, "_get_deck_by_name", find_deck)
    monkeypatch.setattr(decks, "get_or_create_deck", get_or_create)
    return user, created


def test_resolve_apkg_deck_creates_and_reuses_hierarchy(monkeypatch) -> None:
    user, created = _deck_resolver(monkeypatch)

    leaf = asyncio.run(decks.resolve_apkg_deck(SimpleNamespace(), user, "A::B::C"))
    repeated_leaf = asyncio.run(decks.resolve_apkg_deck(SimpleNamespace(), user, "A::B::C"))

    assert list(created) == ["A", "B", "C"]
    assert created["A"].parent_id is None
    assert created["B"].parent_id == created["A"].id
    assert created["C"].parent_id == created["B"].id
    assert leaf is created["C"]
    assert repeated_leaf is created["C"]


def test_resolve_apkg_deck_uses_existing_flat_deck(monkeypatch) -> None:
    user, created = _deck_resolver(monkeypatch)
    flat_deck = Deck(id=1, user_id=user.id, name="A::B::C")
    created[flat_deck.name] = flat_deck

    resolved = asyncio.run(decks.resolve_apkg_deck(SimpleNamespace(), user, flat_deck.name))

    assert resolved is flat_deck
    assert list(created) == ["A::B::C"]


def test_auto_import_places_notes_in_hierarchy_leaf_and_skips_repeat(monkeypatch) -> None:
    user, created = _deck_resolver(monkeypatch)
    imported_decks: list[Deck] = []
    imported_cards: list[Deck] = []

    async def resolve(session, found_user, name: str, description=None):
        return await decks.resolve_apkg_deck(session, found_user, name, description)

    async def note_exists(session, found_user, deck, front: str, back: str) -> bool:
        return deck in imported_decks

    async def create_note_with_cards(*, deck, card_specs, **kwargs) -> None:
        imported_decks.append(deck)
        imported_cards.extend(spec["deck"] for spec in card_specs)

    monkeypatch.setattr(import_cards, "async_session", _SessionContext)
    monkeypatch.setattr(import_cards, "get_or_create_user", lambda session, tg_user: _async_value(user))
    monkeypatch.setattr(import_cards, "resolve_apkg_deck", resolve)
    monkeypatch.setattr(import_cards, "note_exists", note_exists)
    monkeypatch.setattr(import_cards, "create_note_with_cards", create_note_with_cards)

    note = ImportedNote(
        front="Question",
        back="Answer",
        tags=[],
        note_type="basic",
        anki_model_id=None,
        fields={},
        deck_name="A::B::C",
        cards=[ImportedCard(front="Question", back="Answer", tags=[], deck_name="A::B::C")],
    )

    asyncio.run(import_cards._import_notes(_Message(), _State(), [note], "apkg", []))
    asyncio.run(import_cards._import_notes(_Message(), _State(), [note], "apkg", []))

    assert list(created) == ["A", "B", "C"]
    assert imported_decks == [created["C"]]
    assert imported_cards == [created["C"]]


async def _async_value(value):
    return value


def test_deck_full_path_uses_parent_chain() -> None:
    root = Deck(id=1, user_id=1, name="A")
    middle = Deck(id=2, user_id=1, parent_id=root.id, name="B")
    leaf = Deck(id=3, user_id=1, parent_id=middle.id, name="C")

    assert decks.deck_full_path(leaf, {root.id: root, middle.id: middle, leaf.id: leaf}) == "A::B::C"
