import asyncio
import hashlib
import hmac
import json
import sqlite3
import time
import zipfile
from io import BytesIO
from types import SimpleNamespace
from urllib.parse import urlencode

import httpx
from sqlalchemy import select

from app.deps import get_db_session
from app.main import create_app
from bot.handlers import import_cards
from bot.models import NoteStyle
from bot.services.apkg_importer import parse_apkg_media, parse_apkg_notes
from bot.services.decks import create_deck
from bot.services.users import get_or_create_user


TEST_BOT_TOKEN = "test-bot-token"
TEST_TELEGRAM_ID = 987654


class TelegramUser:
    def __init__(self, telegram_id: int) -> None:
        self.id = telegram_id
        self.username = "testuser"
        self.full_name = "Test User"
        self.language_code = "en"


def signed_init_data(telegram_id: int = TEST_TELEGRAM_ID) -> str:
    data = {
        "auth_date": str(int(time.time())),
        "query_id": "test-query",
        "user": json.dumps(
            {"id": telegram_id, "first_name": "Test", "username": "testuser"},
            separators=(",", ":"),
        ),
    }
    data_check_string = "\n".join(f"{key}={value}" for key, value in sorted(data.items()))
    secret_key = hmac.new(b"WebAppData", TEST_BOT_TOKEN.encode(), hashlib.sha256).digest()
    data["hash"] = hmac.new(secret_key, data_check_string.encode(), hashlib.sha256).hexdigest()
    return urlencode(data)


def build_app(session_factory, monkeypatch):
    import app.deps as deps

    async def override_db_session():
        async with session_factory() as session:
            yield session

    monkeypatch.setattr(
        deps,
        "get_settings",
        lambda: SimpleNamespace(bot_token=TEST_BOT_TOKEN, auth_max_age_seconds=86400),
    )
    app = create_app()
    app.dependency_overrides[get_db_session] = override_db_session
    return app


def post_import(app, filename: str, content: bytes, deck_id: str, telegram_id: int = TEST_TELEGRAM_ID):
    async def send():
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            return await client.post(
                "/api/import",
                data={"deck_id": deck_id},
                files={"file": (filename, content)},
                headers={"X-Telegram-Init-Data": signed_init_data(telegram_id)},
            )

    return asyncio.run(send())


def build_apkg(tmp_path, deck_name: str = "Spanish::Verbs", css: str | None = None) -> bytes:
    tmp_path.mkdir(parents=True, exist_ok=True)
    collection = tmp_path / "collection.anki2"
    connection = sqlite3.connect(collection)
    connection.execute("CREATE TABLE col (models text, decks text)")
    connection.execute("CREATE TABLE notes (id integer primary key, guid text, mid integer, flds text, tags text)")
    connection.execute("CREATE TABLE cards (id integer primary key, nid integer, did integer, ord integer)")
    model = {"type": 0, "tmpls": []}
    if css is not None:
        model["css"] = css
    connection.execute(
        "INSERT INTO col(models, decks) VALUES (?, ?)",
        (json.dumps({"1": model}), json.dumps({"42": {"name": deck_name}})),
    )
    connection.execute(
        "INSERT INTO notes(id, guid, mid, flds, tags) VALUES (1, 'stable-guid', 1, ?, '')",
        ("hablo\x1fI speak",),
    )
    connection.execute("INSERT INTO cards(id, nid, did, ord) VALUES (1, 1, 42, 0)")
    connection.commit()
    connection.close()

    package = BytesIO()
    with zipfile.ZipFile(package, "w") as archive:
        archive.write(collection, "collection.anki2")
        archive.writestr("media", json.dumps({"0": "verb.png"}))
        archive.writestr("0", b"media-content")
    return package.getvalue()


def create_user_deck(session_factory, telegram_id: int) -> int:
    async def create() -> int:
        async with session_factory() as session:
            user = await get_or_create_user(session, TelegramUser(telegram_id))
            deck = await create_deck(session, user, "Imported")
            return deck.id

    return asyncio.run(create())


def test_import_apkg_auto_creates_hierarchy_saves_media_and_merges(session_factory, monkeypatch, tmp_path) -> None:
    app = build_app(session_factory, monkeypatch)
    package = build_apkg(tmp_path)

    first = post_import(app, "verbs.apkg", package, "auto")
    second = post_import(app, "verbs.apkg", package, "auto")

    assert first.status_code == 200
    assert first.json() == {
        "added": 1,
        "updated": 0,
        "unchanged": 0,
        "decks_created": ["Spanish", "Spanish::Verbs"],
        "media_saved": 1,
    }
    assert second.status_code == 200
    assert second.json() == {
        "added": 0,
        "updated": 0,
        "unchanged": 1,
        "decks_created": [],
        "media_saved": 0,
    }


def test_import_apkg_upserts_model_css(session_factory, monkeypatch, tmp_path) -> None:
    app = build_app(session_factory, monkeypatch)
    first = post_import(app, "verbs.apkg", build_apkg(tmp_path, css=".card { color: red; }"), "auto")
    second = post_import(
        app,
        "verbs.apkg",
        build_apkg(tmp_path / "updated", css=".card { color: blue; }"),
        "auto",
    )

    async def saved_styles() -> list[NoteStyle]:
        async with session_factory() as session:
            return list((await session.execute(select(NoteStyle))).scalars())

    styles = asyncio.run(saved_styles())

    assert first.status_code == 200
    assert second.status_code == 200
    assert [(style.anki_model_id, style.css) for style in styles] == [
        ("1", ".card { color: blue; }")
    ]


def test_import_csv_requires_specific_deck(session_factory, monkeypatch) -> None:
    app = build_app(session_factory, monkeypatch)
    deck_id = create_user_deck(session_factory, TEST_TELEGRAM_ID)

    response = post_import(app, "cards.csv", b"front,back,tag\nquestion,answer,tag\n", str(deck_id))
    auto_response = post_import(app, "cards.csv", b"question,answer\n", "auto")

    assert response.status_code == 200
    assert response.json()["added"] == 2
    assert auto_response.status_code == 422


def test_import_rejects_invalid_files_and_other_users_deck(session_factory, monkeypatch) -> None:
    app = build_app(session_factory, monkeypatch)
    other_deck_id = create_user_deck(session_factory, TEST_TELEGRAM_ID + 1)

    foreign_deck = post_import(app, "cards.tsv", b"question\tanswer\n", str(other_deck_id))
    unsupported = post_import(app, "cards.xlsx", b"data", "auto")
    oversized = post_import(app, "cards.txt", b"x" * (20 * 1024 * 1024 + 1), str(other_deck_id))
    broken = post_import(app, "broken.apkg", b"not a zip", "auto")

    assert foreign_deck.status_code == 404
    assert unsupported.status_code == 422
    assert oversized.status_code == 413
    assert broken.status_code == 422
    assert any("а" <= character.lower() <= "я" for character in broken.json()["detail"])


def test_import_bot_and_api_report_same_apkg_counts(session_factory, monkeypatch, tmp_path) -> None:
    app = build_app(session_factory, monkeypatch)
    package = build_apkg(tmp_path)
    api_response = post_import(app, "verbs.apkg", package, "auto", TEST_TELEGRAM_ID)
    messages: list[str] = []

    class Message:
        from_user = TelegramUser(TEST_TELEGRAM_ID + 1)

        async def answer(self, text: str) -> None:
            messages.append(text)

    class State:
        async def get_data(self) -> dict:
            return {"deck_id": "auto"}

        async def clear(self) -> None:
            return None

    monkeypatch.setattr(import_cards, "async_session", session_factory)
    asyncio.run(
        import_cards._import_notes(
            Message(), State(), parse_apkg_notes(package), "apkg", parse_apkg_media(package)
        )
    )

    assert api_response.status_code == 200
    assert f"Добавлено: {api_response.json()['added']}" in messages[0]
    assert f"обновлено: {api_response.json()['updated']}" in messages[0]
    assert f"без изменений: {api_response.json()['unchanged']}" in messages[0]
