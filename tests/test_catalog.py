import asyncio
import hashlib
import hmac
import json
import time
from types import SimpleNamespace
from urllib.parse import urlencode

import httpx
from sqlalchemy import select

from app.deps import get_db_session
from app.main import create_app
from bot.models import Card, Deck, Note, SharedDeck, User
from bot.seed_shared_decks import seed_shared_decks
from bot.services.decks import archive_deck, create_deck
from bot.services.users import get_or_create_user

TEST_BOT_TOKEN = "test-bot-token"
TEST_TELEGRAM_ID = 987654


class TelegramUser:
    id = TEST_TELEGRAM_ID
    username = "cataloguser"
    full_name = "Catalog User"
    language_code = "en"


def signed_init_data() -> str:
    data = {
        "auth_date": str(int(time.time())),
        "query_id": "catalog-test-query",
        "user": json.dumps(
            {
                "id": TEST_TELEGRAM_ID,
                "first_name": "Catalog",
                "last_name": "User",
                "username": "cataloguser",
                "language_code": "en",
            },
            separators=(",", ":"),
        ),
    }
    data_check_string = "\n".join(f"{key}={value}" for key, value in sorted(data.items()))
    secret_key = hmac.new(b"WebAppData", TEST_BOT_TOKEN.encode(), hashlib.sha256).digest()
    data["hash"] = hmac.new(
        secret_key, data_check_string.encode(), hashlib.sha256
    ).hexdigest()
    return urlencode(data)


def build_app(session_factory, monkeypatch):
    import app.deps as deps

    async def override_db_session():
        async with session_factory() as session:
            yield session

    monkeypatch.setattr(
        deps,
        "get_settings",
        lambda: SimpleNamespace(
            bot_token=TEST_BOT_TOKEN,
            auth_max_age_seconds=86400,
        ),
    )
    app = create_app()
    app.dependency_overrides[get_db_session] = override_db_session
    return app


def request(app, method: str, path: str, headers: dict[str, str] | None = None) -> httpx.Response:
    async def send() -> httpx.Response:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            return await client.request(method, path, headers=headers)

    return asyncio.run(send())


def test_shared_deck_seed_is_idempotent_and_continues_after_invalid_file(
    session_factory, tmp_path
) -> None:
    valid_path = tmp_path / "valid.json"
    valid_path.write_text(
        json.dumps(
            {
                "slug": "starter",
                "title": "Starter",
                "description": "A starter deck",
                "language": "en",
                "tags": ["shared"],
                "notes": [{"front": "one", "back": "один", "reverse": True}],
            }
        ),
        encoding="utf-8",
    )
    (tmp_path / "broken.json").write_text('{"title": "Broken"}', encoding="utf-8")

    first = asyncio.run(seed_shared_decks(tmp_path, session_factory))
    second = asyncio.run(seed_shared_decks(tmp_path, session_factory))
    valid_path.write_text(
        valid_path.read_text(encoding="utf-8").replace("Starter", "Updated starter"),
        encoding="utf-8",
    )
    third = asyncio.run(seed_shared_decks(tmp_path, session_factory))

    async def fetch_decks() -> list[SharedDeck]:
        async with session_factory() as session:
            return list((await session.execute(select(SharedDeck))).scalars())

    decks = asyncio.run(fetch_decks())
    assert [(result.filename, result.status) for result in first] == [
        ("broken.json", "error"),
        ("valid.json", "created"),
    ]
    assert [(result.filename, result.status) for result in second] == [
        ("broken.json", "error"),
        ("valid.json", "unchanged"),
    ]
    assert [(result.filename, result.status) for result in third] == [
        ("broken.json", "error"),
        ("valid.json", "updated"),
    ]
    assert [(deck.slug, deck.title, deck.notes_count) for deck in decks] == [
        ("starter", "Updated starter", 1)
    ]


def test_catalog_api_lists_installs_and_reinstalls_decks(session_factory, monkeypatch) -> None:
    async def create_data() -> None:
        async with session_factory() as session:
            session.add_all(
                [
                    SharedDeck(
                        slug="zeta",
                        title="Zeta",
                        description="Zeta deck",
                        language="en",
                        tags=["zeta"],
                        notes=[],
                        notes_count=0,
                    ),
                    SharedDeck(
                        slug="catalog",
                        title="Catalog",
                        description="Catalog deck",
                        language="en",
                        tags=["shared"],
                        notes=[
                            {
                                "front": "one",
                                "back": "один",
                                "reverse": True,
                                "tags": ["number"],
                            },
                            {"front": "two", "back": "два", "reverse": False},
                        ],
                        notes_count=2,
                    ),
                ]
            )
            user = await get_or_create_user(session, TelegramUser())
            await create_deck(session, user, "Catalog")
            await session.commit()

    asyncio.run(create_data())
    app = build_app(session_factory, monkeypatch)
    headers = {"X-Telegram-Init-Data": signed_init_data()}

    unauthorized = request(app, "GET", "/api/catalog")
    listed = request(app, "GET", "/api/catalog", headers)
    unknown = request(app, "POST", "/api/catalog/missing/install", headers)
    installed = request(app, "POST", "/api/catalog/catalog/install", headers)
    repeated = request(app, "POST", "/api/catalog/catalog/install", headers)
    after_install = request(app, "GET", "/api/catalog", headers)

    assert unauthorized.status_code == 401
    assert listed.status_code == 200
    assert [item["slug"] for item in listed.json()] == ["catalog", "zeta"]
    assert listed.json()[0]["notes_count"] == 2
    assert listed.json()[0]["installed"] is False
    assert unknown.status_code == 404
    assert installed.status_code == 200
    assert installed.json()["added"] == 2
    assert repeated.status_code == 409
    assert after_install.json()[0]["installed"] is True

    async def inspect_install() -> tuple[Deck, list[Note], list[Card]]:
        async with session_factory() as session:
            user = await session.scalar(
                select(User).where(User.telegram_id == TEST_TELEGRAM_ID)
            )
            assert user is not None
            deck = await session.scalar(
                select(Deck).where(
                    Deck.user_id == user.id,
                    Deck.source_slug == "catalog",
                    Deck.is_archived.is_(False),
                )
            )
            assert deck is not None
            notes = list((await session.execute(select(Note).where(Note.deck_id == deck.id))).scalars())
            cards = list((await session.execute(select(Card).where(Card.deck_id == deck.id))).scalars())
            return deck, notes, cards

    deck, notes, cards = asyncio.run(inspect_install())
    assert deck.name == "Catalog (catalog)"
    assert deck.source_slug == "catalog"
    assert sorted(note.tags for note in notes) == [["shared"], ["shared", "number"]]
    assert len(cards) == 3
    assert all(card.state == "new" and card.fsrs_data is not None for card in cards)

    async def archive_installed_deck() -> None:
        async with session_factory() as session:
            installed_deck = await session.scalar(select(Deck).where(Deck.id == deck.id))
            assert installed_deck is not None
            await archive_deck(session, installed_deck)

    asyncio.run(archive_installed_deck())
    reinstalled = request(app, "POST", "/api/catalog/catalog/install", headers)
    assert reinstalled.status_code == 200
    assert reinstalled.json()["deck_id"] != deck.id
