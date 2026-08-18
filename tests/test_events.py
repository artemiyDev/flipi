import asyncio
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import httpx
from sqlalchemy import select

from app.deps import get_db_session
from app.main import create_app
from bot.handlers import cards, common, decks, import_cards, reminders
from bot.models import Card, Deck, Event, SharedDeck, User
from bot.services.apkg_importer import ImportedCard
from bot.services.cards import create_basic_note
from bot.services.catalog import install_catalog_deck
from bot.services.decks import create_deck
from bot.services.events import track
from bot.services.reminders import send_due_reminders
from bot.services.study import answer_card


class TelegramUser:
    id = 7001
    username = "events"
    full_name = "Events User"
    language_code = "en"


class State:
    def __init__(self, data: dict | None = None) -> None:
        self.data = data or {}

    async def clear(self) -> None:
        self.data = {}

    async def get_data(self) -> dict:
        return self.data


class Message:
    def __init__(self) -> None:
        self.from_user = TelegramUser()
        self.text = "-"
        self.answers: list[str] = []

    async def answer(self, text: str, **kwargs) -> None:
        self.answers.append(text)


class Callback:
    def __init__(self, data: str) -> None:
        self.from_user = TelegramUser()
        self.data = data
        self.message = Message()

    async def answer(self, *args, **kwargs) -> None:
        return None


async def _events(session_factory) -> list[Event]:
    async with session_factory() as session:
        return list((await session.scalars(select(Event).order_by(Event.id))).all())


def test_bot_actions_and_services_record_events(session_factory, monkeypatch) -> None:
    monkeypatch.setattr(common, "async_session", session_factory)
    monkeypatch.setattr(decks, "async_session", session_factory)
    monkeypatch.setattr(cards, "async_session", session_factory)
    monkeypatch.setattr(import_cards, "async_session", session_factory)
    monkeypatch.setattr(reminders, "async_session", session_factory)

    async def check() -> None:
        await common.start(Message(), State())

        deck_message = Message()
        await decks.add_deck_description(deck_message, State({"name": "Manual"}))

        async with session_factory() as session:
            user = (await session.scalars(select(User))).one()
            deck = (await session.scalars(select(Deck))).one()
            deck_id = deck.id

        await cards.add_card_finish(
            Callback("card:reverse:yes"),
            State({"deck_id": deck_id, "front": "front", "back": "back", "tags": []}),
        )
        await import_cards._import_rows(
            Message(),
            State({"deck_id": deck_id}),
            [ImportedCard(front="import", back="done", tags=[], create_reverse=False)],
            source="import",
            format="csv",
            media_files=[],
        )
        await reminders.snooze(Callback("reminder:snooze"))
        await reminders.skip_today(Callback("reminder:skip"))

        async with session_factory() as session:
            user = (await session.scalars(select(User))).one()
            deck = await create_deck(session, user, "Study")
            note = await create_basic_note(session, user, deck, "question", "answer")
            card = (await session.scalars(select(Card).where(Card.note_id == note.id))).one()
            await answer_card(session, user, card, 3)

            session.add(
                SharedDeck(
                    slug="shared",
                    title="Shared",
                    description="Shared deck",
                    language="en",
                    tags=[],
                    notes=[{"front": "one", "back": "two", "reverse": False}],
                    notes_count=1,
                )
            )
            await session.commit()
            await install_catalog_deck(session, user, "shared")

            reminder_user = User(
                telegram_id=7002,
                timezone="UTC",
                reminder_enabled=True,
                reminder_minutes_local=0,
            )
            session.add(reminder_user)
            await session.flush()
            reminder_deck = await create_deck(session, reminder_user, "Reminder")
            reminder_note = await create_basic_note(
                session, reminder_user, reminder_deck, "due", "card"
            )
            reminder_card = (
                await session.scalars(select(Card).where(Card.note_id == reminder_note.id))
            ).one()
            reminder_card.due_at = datetime.now(UTC) - timedelta(minutes=1)
            await session.commit()

        class FakeBot:
            async def send_message(self, chat_id: int, text: str, **kwargs) -> None:
                return None

        await send_due_reminders(FakeBot(), session_factory, "https://example.test/app")

        rows = await _events(session_factory)
        by_name = {event.name: event for event in rows}
        assert by_name["bot_start"].props == {"source": "start"}
        assert by_name["deck_created"].props is None
        assert by_name["card_created"].props == {"reverse": True}
        assert by_name["import_done"].props == {
            "format": "csv",
            "added": 1,
            "updated": 0,
            "unchanged": 0,
        }
        assert by_name["review_answer"].props["rating"] == 3
        assert by_name["review_answer"].props["state_after"] in {"learning", "review"}
        assert by_name["catalog_install"].props == {"slug": "shared", "added": 1}
        assert by_name["reminder_sent"].props["due"] >= 1
        assert {event.props["action"] for event in rows if event.name == "reminder_clicked"} == {
            "snooze",
            "skip",
        }

    asyncio.run(check())


def test_app_open_is_deduplicated_and_tracking_failure_does_not_break_api(session_factory, monkeypatch) -> None:
    import app.deps as deps

    monkeypatch.setattr(
        deps,
        "get_settings",
        lambda: SimpleNamespace(bot_token="test", auth_max_age_seconds=60),
    )
    monkeypatch.setattr(
        deps,
        "validate_init_data",
        lambda *args: TelegramUser(),
    )
    deps._app_opened_at.clear()
    original_monotonic = deps.monotonic
    ticks = iter((1.0, 2.0, 1801.0))
    monkeypatch.setattr(deps, "monotonic", lambda: next(ticks))

    async def check_deduplication() -> None:
        async with session_factory() as session:
            first = await deps.get_current_user(session, "valid")
            second = await deps.get_current_user(session, "valid")
            third = await deps.get_current_user(session, "valid")
            assert first.id == second.id == third.id
        rows = await _events(session_factory)
        assert [event.name for event in rows] == ["app_open", "app_open"]

    asyncio.run(check_deduplication())
    monkeypatch.setattr(deps, "monotonic", original_monotonic)

    async def broken_track(*args, **kwargs) -> None:
        raise RuntimeError("analytics unavailable")

    monkeypatch.setattr(deps, "track", broken_track)

    async def override_db_session():
        async with session_factory() as session:
            yield session

    app = create_app()
    app.dependency_overrides[get_db_session] = override_db_session

    async def request() -> httpx.Response:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            return await client.get("/api/me", headers={"X-Telegram-Init-Data": "valid"})

    assert asyncio.run(request()).status_code == 200


def test_track_swallows_session_errors() -> None:
    class BrokenSession:
        def add(self, event) -> None:
            raise RuntimeError("database unavailable")

    asyncio.run(track(BrokenSession(), 1, "test"))
