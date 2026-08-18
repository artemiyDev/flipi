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
from bot.handlers import common
from bot.models import Card, Deck, Event, Note, NoteStyle, User
from bot.services.cards import create_basic_note
from bot.services.decks import archive_deck, create_deck
from bot.services.shares import create_or_get_share


TEST_BOT_TOKEN = "test-bot-token"
OWNER_TELEGRAM_ID = 11001
RECIPIENT_TELEGRAM_ID = 11002


def signed_init_data(telegram_id: int, username: str, first_name: str) -> str:
    data = {
        "auth_date": str(int(time.time())),
        "query_id": f"share-test-{telegram_id}",
        "user": json.dumps(
            {
                "id": telegram_id,
                "first_name": first_name,
                "last_name": "User",
                "username": username,
                "language_code": "en",
            },
            separators=(",", ":"),
        ),
    }
    data_check_string = "\n".join(f"{key}={value}" for key, value in sorted(data.items()))
    secret_key = hmac.new(b"WebAppData", TEST_BOT_TOKEN.encode(), hashlib.sha256).digest()
    data["hash"] = hmac.new(secret_key, data_check_string.encode(), hashlib.sha256).hexdigest()
    return urlencode(data)


def build_app(session_factory, monkeypatch):
    import app.api as api
    import app.deps as deps

    async def override_db_session():
        async with session_factory() as session:
            yield session

    settings = SimpleNamespace(
        bot_token=TEST_BOT_TOKEN,
        auth_max_age_seconds=86400,
        bot_username="flipibot",
    )
    monkeypatch.setattr(deps, "get_settings", lambda: settings)
    monkeypatch.setattr(api, "get_settings", lambda: settings)
    app = create_app()
    app.dependency_overrides[get_db_session] = override_db_session
    return app


def request(
    app,
    method: str,
    path: str,
    headers: dict[str, str],
    payload: dict | None = None,
) -> httpx.Response:
    async def send() -> httpx.Response:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            return await client.request(method, path, headers=headers, json=payload)

    return asyncio.run(send())


def test_share_api_installs_a_snapshot_with_attribution_and_events(session_factory, monkeypatch) -> None:
    async def create_data() -> tuple[int, int, int]:
        async with session_factory() as session:
            owner = User(
                telegram_id=OWNER_TELEGRAM_ID,
                username="owner",
                full_name="Owner User",
            )
            recipient = User(
                telegram_id=RECIPIENT_TELEGRAM_ID,
                username="recipient",
                full_name="Recipient User",
            )
            session.add_all([owner, recipient])
            await session.flush()
            source_deck = await create_deck(session, owner, "Shared deck", "Snapshot source")
            source_note = await create_basic_note(
                session,
                owner,
                source_deck,
                "Original front",
                "Original back",
                tags=["shared"],
                create_reverse=True,
                anki_model_id="basic-model",
                fields={"Front": "Original front", "Back": "Original back"},
                template_name="Basic",
                question_template="{{Front}}",
                answer_template="{{Back}}",
            )
            source_cards = list(
                (await session.scalars(select(Card).where(Card.note_id == source_note.id))).all()
            )
            source_cards[0].state = "review"
            source_cards[0].reps = 4
            source_cards[0].fsrs_data = {"stability": 12}
            source_note.anki_guid = "source-guid"
            session.add(NoteStyle(user_id=owner.id, anki_model_id="basic-model", css=".card { color: red; }"))
            await create_deck(session, recipient, "Shared deck")
            await session.commit()
            return source_deck.id, source_note.id, owner.id

    source_deck_id, source_note_id, owner_user_id = asyncio.run(create_data())
    app = build_app(session_factory, monkeypatch)
    owner_headers = {"X-Telegram-Init-Data": signed_init_data(OWNER_TELEGRAM_ID, "owner", "Owner")}
    recipient_headers = {
        "X-Telegram-Init-Data": signed_init_data(RECIPIENT_TELEGRAM_ID, "recipient", "Recipient")
    }

    first_share = request(app, "POST", f"/api/decks/{source_deck_id}/share", owner_headers)
    second_share = request(app, "POST", f"/api/decks/{source_deck_id}/share", owner_headers)
    token = first_share.json()["token"]

    assert first_share.status_code == 200
    assert second_share.status_code == 200
    assert second_share.json()["token"] == token
    assert first_share.json()["link"] == f"https://t.me/flipibot?start=deck_{token}"
    import app.api as api

    monkeypatch.setattr(api, "get_settings", lambda: SimpleNamespace(bot_username=""))
    assert request(app, "POST", f"/api/decks/{source_deck_id}/share", owner_headers).json()["link"] is None
    assert request(app, "POST", f"/api/decks/{source_deck_id}/share", recipient_headers).status_code == 404

    owner_preview = request(app, "GET", f"/api/share/{token}", owner_headers)
    recipient_preview = request(app, "GET", f"/api/share/{token}", recipient_headers)
    assert owner_preview.json() == {
        "title": "Shared deck",
        "description": "Snapshot source",
        "cards_count": 2,
        "author": "Owner User",
        "installed": False,
        "own": True,
    }
    assert recipient_preview.json()["own"] is False
    assert recipient_preview.json()["installed"] is False
    assert request(app, "GET", "/api/share/missing", recipient_headers).status_code == 404

    installed = request(app, "POST", f"/api/share/{token}/install", recipient_headers)
    assert installed.status_code == 200
    assert installed.json()["added"] == 1
    assert request(app, "POST", f"/api/share/{token}/install", recipient_headers).status_code == 409
    assert request(app, "POST", f"/api/share/{token}/install", owner_headers).status_code == 409
    assert request(app, "GET", f"/api/share/{token}", recipient_headers).json()["installed"] is True

    async def inspect_install() -> tuple[Deck, Note, list[Card], NoteStyle, list[Event]]:
        async with session_factory() as session:
            recipient = await session.scalar(
                select(User).where(User.telegram_id == RECIPIENT_TELEGRAM_ID)
            )
            assert recipient is not None
            copied_deck = await session.scalar(
                select(Deck).where(Deck.user_id == recipient.id, Deck.source_slug == f"share:{token}")
            )
            assert copied_deck is not None
            copied_note = await session.scalar(select(Note).where(Note.deck_id == copied_deck.id))
            assert copied_note is not None
            copied_cards = list(
                (await session.scalars(select(Card).where(Card.deck_id == copied_deck.id))).all()
            )
            copied_style = await session.scalar(
                select(NoteStyle).where(
                    NoteStyle.user_id == recipient.id,
                    NoteStyle.anki_model_id == "basic-model",
                )
            )
            assert copied_style is not None
            events = list((await session.scalars(select(Event).order_by(Event.id))).all())
            return copied_deck, copied_note, copied_cards, copied_style, events

    copied_deck, copied_note, copied_cards, copied_style, events = asyncio.run(inspect_install())
    assert copied_deck.name == "Shared deck (share)"
    assert copied_note.anki_guid is None
    assert copied_style.css == ".card { color: red; }"
    assert len(copied_cards) == 2
    assert all(card.state == "new" and card.reps == 0 for card in copied_cards)
    assert all(card.fsrs_data != {"stability": 12} for card in copied_cards)
    by_name = {}
    for event in events:
        by_name.setdefault(event.name, []).append(event)
    assert by_name["share_created"][0].props == {"deck_id": source_deck_id}
    assert by_name["share_installed"][0].props == {
        "token": token,
        "owner_user_id": owner_user_id,
        "added": 1,
    }
    assert any(event.props == {"token": token} for event in by_name["share_opened"])

    async def change_source() -> None:
        async with session_factory() as session:
            source_note = await session.scalar(select(Note).where(Note.id == source_note_id))
            assert source_note is not None
            source_note.front = "Changed after install"
            await session.commit()

    asyncio.run(change_source())

    async def copied_front() -> str:
        async with session_factory() as session:
            note = await session.scalar(select(Note).where(Note.deck_id == copied_deck.id))
            assert note is not None
            return note.front

    assert asyncio.run(copied_front()) == "Original front"

    async def archive_source() -> None:
        async with session_factory() as session:
            source = await session.scalar(select(Deck).where(Deck.id == source_deck_id))
            assert source is not None
            await archive_deck(session, source)

    asyncio.run(archive_source())
    assert request(app, "POST", f"/api/decks/{source_deck_id}/share", owner_headers).status_code == 409


class TelegramUser:
    id = OWNER_TELEGRAM_ID
    username = "owner"
    full_name = "Owner User"
    language_code = "en"


class State:
    async def clear(self) -> None:
        return None


class Message:
    def __init__(self, text: str) -> None:
        self.from_user = TelegramUser()
        self.text = text
        self.answers: list[tuple[str, dict]] = []

    async def answer(self, text: str, **kwargs) -> None:
        self.answers.append((text, kwargs))


def test_share_start_link_shows_preview_or_stale_message(session_factory, monkeypatch) -> None:
    async def create_data() -> str:
        async with session_factory() as session:
            owner = User(telegram_id=OWNER_TELEGRAM_ID, username="owner", full_name="Owner User")
            session.add(owner)
            await session.flush()
            deck = await create_deck(session, owner, "Bot share")
            await create_basic_note(session, owner, deck, "front", "back")
            share, _ = await create_or_get_share(session, deck, owner)
            await session.commit()
            return share.token

    token = asyncio.run(create_data())
    monkeypatch.setattr(common, "async_session", session_factory)
    monkeypatch.setattr(common, "get_settings", lambda: SimpleNamespace(web_app_url="https://app.test"))

    valid_message = Message(f"/start deck_{token}")
    invalid_message = Message("/start deck_missing")
    asyncio.run(common.start(valid_message, State()))
    asyncio.run(common.start(invalid_message, State()))

    valid_text, valid_kwargs = valid_message.answers[0]
    assert valid_text == "Bot share — 1 карточек · от Owner User"
    button = valid_kwargs["reply_markup"].inline_keyboard[0][0]
    assert button.text == "Открыть в приложении"
    assert button.web_app is not None
    assert button.web_app.url == f"https://app.test?share={token}"
    assert "Ссылка на колоду устарела." in invalid_message.answers[0][0]
