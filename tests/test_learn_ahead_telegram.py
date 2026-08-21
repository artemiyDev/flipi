import asyncio
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

from sqlalchemy import select

from bot.handlers import study
from bot.models import Card, ReviewLog
from bot.services.cards import create_basic_note
from bot.services.decks import create_deck
from bot.services.users import get_or_create_user

NOW = datetime(2026, 8, 21, 16, 0, tzinfo=UTC)


class TelegramUser:
    id = 26101
    username = "learn-ahead-tg"
    full_name = "Learn Ahead Telegram"
    language_code = "en"


class State:
    def __init__(self, data: dict | None = None) -> None:
        self.data = data or {}
        self.state = None

    async def get_data(self) -> dict:
        return self.data

    async def update_data(self, **kwargs) -> None:
        self.data.update(kwargs)

    async def set_state(self, value) -> None:
        self.state = value


class Message:
    def __init__(self, message_id: int, text: str | None = None) -> None:
        self.from_user = TelegramUser()
        self.chat = SimpleNamespace(id=TelegramUser.id)
        self.message_id = message_id
        self.text = text
        self.answers: list[tuple[str, dict]] = []

    async def answer(self, text: str, **kwargs) -> None:
        self.answers.append((text, kwargs))

    async def answer_photo(self, *args, **kwargs) -> None:
        raise AssertionError("Unexpected media")

    async def answer_audio(self, *args, **kwargs) -> None:
        raise AssertionError("Unexpected media")

    async def answer_document(self, *args, **kwargs) -> None:
        raise AssertionError("Unexpected media")


class Callback:
    def __init__(self, data: str, message: Message) -> None:
        self.from_user = TelegramUser()
        self.data = data
        self.message = message

    async def answer(self, *args, **kwargs) -> None:
        return None


async def _make_card(session, user, deck, front: str, state: str, due_at: datetime, tags=None):
    note = await create_basic_note(session, user, deck, front, "answer", tags=tags)
    card = (
        await session.scalars(select(Card).where(Card.note_id == note.id))
    ).one()
    card.state = state
    card.due_at = due_at
    await session.flush()
    return card


def _freeze_handler_clock(monkeypatch) -> None:
    class FixedDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            return NOW if tz is not None else NOW.replace(tzinfo=None)

    monkeypatch.setattr(study, "datetime", FixedDateTime)


def _question(messages: list[tuple[str, dict]]) -> str:
    return next(text for text, _ in messages if "<b>Вопрос</b>" in text)


def test_telegram_all_no_longer_crashes_and_deck_all_share_due_first_hint_policy(
    session_factory,
    monkeypatch,
) -> None:
    monkeypatch.setattr(study, "async_session", session_factory)
    _freeze_handler_clock(monkeypatch)

    async def create_data() -> tuple[int, int, int]:
        async with session_factory() as session:
            user = await get_or_create_user(session, TelegramUser())
            alpha = await create_deck(session, user, "Alpha")
            beta = await create_deck(session, user, "Beta")
            ahead = await _make_card(
                session, user, alpha, "ahead", "learning", NOW + timedelta(minutes=8)
            )
            due = await _make_card(session, user, beta, "due", "new", NOW)
            await session.commit()
            return alpha.id, ahead.id, due.id

    alpha_id, ahead_id, due_id = asyncio.run(create_data())

    async def check() -> None:
        all_due_message = Message(101)
        await study.start_study(
            Callback("study:start:all", all_due_message),
            State(),
        )
        due_question = _question(all_due_message.answers)
        assert "<b>Beta</b>" in due_question
        assert "Повтор чуть раньше" not in due_question

        async with session_factory() as session:
            due = await session.get(Card, due_id)
            due.suspended = True
            await session.commit()

        all_ahead_message = Message(102)
        all_state = State()
        await study.start_study(
            Callback("study:start:all", all_ahead_message),
            all_state,
        )
        assert all_state.data["study_scope"] == "all"
        ahead_question = _question(all_ahead_message.answers)
        assert "<b>Alpha</b>" in ahead_question
        assert ahead_question.startswith("Повтор чуть раньше · через 8 мин")

        deck_message = Message(103)
        await study.start_study(
            Callback(f"study:start:{alpha_id}", deck_message),
            State(),
        )
        assert _question(deck_message.answers).startswith(
            "Повтор чуть раньше · через 8 мин"
        )
        assert ahead_id != due_id

    asyncio.run(check())


def test_telegram_filtered_session_counts_and_shows_only_query_ahead_card(
    session_factory,
    monkeypatch,
) -> None:
    monkeypatch.setattr(study, "async_session", session_factory)
    _freeze_handler_clock(monkeypatch)

    async def create_data() -> int:
        async with session_factory() as session:
            user = await get_or_create_user(session, TelegramUser())
            deck = await create_deck(session, user, "Filtered")
            target = await _make_card(
                session,
                user,
                deck,
                "target",
                "relearning",
                NOW + timedelta(minutes=7),
                tags=["target"],
            )
            await _make_card(
                session,
                user,
                deck,
                "other",
                "learning",
                NOW + timedelta(minutes=1),
                tags=["other"],
            )
            await session.commit()
            return target.id

    target_id = asyncio.run(create_data())

    async def check() -> None:
        state = State()
        message = Message(201, text="tag:target")
        await study.filtered_study_query(message, state)

        assert state.data == {"study_scope": "filter", "filter_query": "tag:target"}
        assert any("Доступно сейчас: 1" in text for text, _ in message.answers)
        question = _question(message.answers)
        assert question.startswith("Повтор чуть раньше · через 7 мин")
        assert "target" in question

        strict_message = Message(202, text="tag:target is:due")
        await study.filtered_study_query(strict_message, State())
        assert any("Доступно сейчас: 0" in text for text, _ in strict_message.answers)
        assert all("<b>Вопрос</b>" not in text for text, _ in strict_message.answers)
        assert target_id > 0

    asyncio.run(check())


def test_telegram_explicit_custom_study_never_gets_standard_learn_ahead_hint(
    session_factory,
    monkeypatch,
) -> None:
    monkeypatch.setattr(study, "async_session", session_factory)

    async def create_data() -> int:
        async with session_factory() as session:
            user = await get_or_create_user(session, TelegramUser())
            deck = await create_deck(session, user, "Custom")
            await _make_card(
                session,
                user,
                deck,
                "future review",
                "review",
                datetime.now(UTC) + timedelta(days=2),
            )
            await _make_card(
                session,
                user,
                deck,
                "due new",
                "new",
                datetime.now(UTC) - timedelta(seconds=1),
            )
            await session.commit()
            return deck.id

    deck_id = asyncio.run(create_data())

    async def check() -> None:
        review_message = Message(301)
        await study.start_review_ahead(
            Callback(f"study:ahead:{deck_id}", review_message),
            State(),
        )
        assert any("Custom study: повторение заранее" in text for text, _ in review_message.answers)
        assert "Повтор чуть раньше" not in _question(review_message.answers)

        new_message = Message(302)
        await study.start_new_without_limit(
            Callback(f"study:new:{deck_id}", new_message),
            State(),
        )
        assert any("Custom study: новые карточки" in text for text, _ in new_message.answers)
        assert "Повтор чуть раньше" not in _question(new_message.answers)

    asyncio.run(check())


def test_telegram_early_rating_double_tap_keeps_request_id_idempotency(
    session_factory,
    monkeypatch,
) -> None:
    monkeypatch.setattr(study, "async_session", session_factory)
    now = datetime.now(UTC)

    async def create_data() -> tuple[int, int]:
        async with session_factory() as session:
            user = await get_or_create_user(session, TelegramUser())
            deck = await create_deck(session, user, "Early rating")
            card = await _make_card(
                session, user, deck, "early", "learning", now + timedelta(minutes=5)
            )
            await session.commit()
            return deck.id, card.id

    deck_id, card_id = asyncio.run(create_data())
    message = Message(401)
    callback = Callback(f"study:rate:{card_id}:3", message)
    state = State({"study_scope": "deck", "deck_id": deck_id})

    async def check() -> None:
        await study.rate_study_card(callback, state)
        await study.rate_study_card(callback, state)
        async with session_factory() as session:
            card = await session.get(Card, card_id)
            reviews = list((await session.scalars(select(ReviewLog))).all())
            assert card.reps == 1
            assert len(reviews) == 1
            assert reviews[0].request_id == f"tg:{TelegramUser.id}:401"

    asyncio.run(check())
