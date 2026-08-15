import asyncio
import hashlib
import hmac
import json
import time
from types import SimpleNamespace
from urllib.parse import urlencode

import httpx

from app.deps import get_db_session
from app.main import create_app
from bot.models import User
from bot.services.decks import archive_deck, create_deck
from bot.services.users import get_or_create_user

TEST_BOT_TOKEN = "test-bot-token"
TEST_TELEGRAM_ID = 123456789


class TelegramUser:
    id = TEST_TELEGRAM_ID
    username = "testuser"
    full_name = "Test User"
    language_code = "en"


def signed_init_data(auth_date: int | None = None) -> str:
    data = {
        "auth_date": str(auth_date if auth_date is not None else int(time.time())),
        "query_id": "test-query",
        "user": json.dumps(
            {
                "id": TEST_TELEGRAM_ID,
                "first_name": "Test",
                "last_name": "User",
                "username": "testuser",
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


def request(app, path: str, headers: dict[str, str] | None = None) -> httpx.Response:
    async def send() -> httpx.Response:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            return await client.get(path, headers=headers)

    return asyncio.run(send())


def test_api_rejects_missing_init_data(session_factory, monkeypatch) -> None:
    response = request(build_app(session_factory, monkeypatch), "/api/me")

    assert response.status_code == 401


def test_api_rejects_invalid_init_data_hash(session_factory, monkeypatch) -> None:
    init_data = signed_init_data().replace("hash=", "hash=invalid")
    response = request(
        build_app(session_factory, monkeypatch),
        "/api/me",
        {"X-Telegram-Init-Data": init_data},
    )

    assert response.status_code == 401


def test_api_rejects_expired_init_data(session_factory, monkeypatch) -> None:
    response = request(
        build_app(session_factory, monkeypatch),
        "/api/me",
        {"X-Telegram-Init-Data": signed_init_data(int(time.time()) - 86401)},
    )

    assert response.status_code == 401


def test_api_creates_user_once_for_valid_init_data(session_factory, monkeypatch) -> None:
    app = build_app(session_factory, monkeypatch)
    headers = {"X-Telegram-Init-Data": signed_init_data()}

    first = request(app, "/api/me", headers)
    second = request(app, "/api/me", headers)

    async def count_users() -> int:
        async with session_factory() as session:
            return len((await session.execute(User.__table__.select())).all())

    assert first.status_code == 200
    assert second.status_code == 200
    assert first.json() == second.json()
    assert asyncio.run(count_users()) == 1


def test_api_lists_active_decks_with_counts(session_factory, monkeypatch) -> None:
    from bot.services.cards import create_basic_note

    async def create_data() -> None:
        async with session_factory() as session:
            user = await get_or_create_user(session, TelegramUser())
            parent = await create_deck(session, user, "Spanish")
            child = await create_deck(session, user, "Verbs", parent=parent)
            archived = await create_deck(session, user, "Archived")
            await create_basic_note(session, user, child, "hablar", "to speak")
            await archive_deck(session, archived)

    asyncio.run(create_data())
    response = request(
        build_app(session_factory, monkeypatch),
        "/api/decks",
        {"X-Telegram-Init-Data": signed_init_data()},
    )

    assert response.status_code == 200
    decks = {deck["name"]: deck for deck in response.json()}
    assert "Archived" not in decks
    assert decks["Spanish::Verbs"]["new_count"] == 1
    assert decks["Spanish::Verbs"]["learning_count"] == 0
    assert decks["Spanish::Verbs"]["review_count"] == 0


def test_healthz_does_not_require_authorization(session_factory, monkeypatch) -> None:
    response = request(build_app(session_factory, monkeypatch), "/api/healthz")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "database": True}
