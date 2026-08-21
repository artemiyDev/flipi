import asyncio
from types import SimpleNamespace

from sqlalchemy import func, select

from bot.handlers import study
from bot.models import Card, DailyStudyCounter, Event, ReviewLog
from bot.services.cards import create_basic_note
from bot.services.decks import create_deck
from bot.services.users import get_or_create_user


class TelegramUser:
    id = 8201
    username = "telegram-leech"
    full_name = "Telegram Leech"
    language_code = "en"


class State:
    def __init__(self, data: dict | None = None) -> None:
        self.data = data or {}

    async def get_data(self) -> dict:
        return self.data


class Message:
    def __init__(self, message_id: int) -> None:
        self.chat = SimpleNamespace(id=TelegramUser.id)
        self.message_id = message_id
        self.answers: list[tuple[str, dict]] = []

    async def answer(self, text: str, **kwargs) -> None:
        self.answers.append((text, kwargs))


class Callback:
    def __init__(self, data: str, message: Message) -> None:
        self.from_user = TelegramUser()
        self.data = data
        self.message = message

    async def answer(self, *args, **kwargs) -> None:
        return None


async def _create_study_cards(session_factory) -> tuple[int, int, int]:
    async with session_factory() as session:
        user = await get_or_create_user(session, TelegramUser())
        deck = await create_deck(session, user, "Telegram study")
        leech_note = await create_basic_note(session, user, deck, "leech", "answer")
        next_note = await create_basic_note(session, user, deck, "next", "answer")
        leech_card = (
            await session.scalars(select(Card).where(Card.note_id == leech_note.id))
        ).one()
        next_card = (
            await session.scalars(select(Card).where(Card.note_id == next_note.id))
        ).one()
        leech_card.state = "review"
        leech_card.review_lapses = 3
        await session.commit()
        return deck.id, leech_card.id, next_card.id


def test_rating_message_is_idempotent_and_leech_replay_shows_rescue(
    session_factory,
    monkeypatch,
) -> None:
    monkeypatch.setattr(study, "async_session", session_factory)
    deck_id, card_id, _ = asyncio.run(_create_study_cards(session_factory))
    message = Message(message_id=501)
    state = State({"study_scope": "deck", "deck_id": deck_id})
    same_rating = Callback(f"study:rate:{card_id}:1", message)
    other_rating = Callback(f"study:rate:{card_id}:3", message)

    async def check() -> None:
        await study.rate_study_card(same_rating, state)
        await study.rate_study_card(same_rating, state)
        await study.rate_study_card(other_rating, state)

        rescue_messages = [
            (text, kwargs)
            for text, kwargs in message.answers
            if "Карточка забыта 4 раза" in text
        ]
        assert len(rescue_messages) == 2
        rescue_buttons = [
            button
            for row in rescue_messages[0][1]["reply_markup"].inline_keyboard
            for button in row
        ]
        assert [button.text for button in rescue_buttons] == [
            "Исправить карточку",
            "Продолжить учить",
            "Оставить на потом",
        ]
        assert rescue_buttons[0].callback_data.startswith("note:edit:")
        assert rescue_buttons[0].callback_data.endswith(f":{card_id}")
        assert [button.callback_data for button in rescue_buttons[1:]] == [
            f"leech:resume:{card_id}:4",
            f"leech:later:{card_id}:4",
        ]
        assert any("другой оценкой" in text for text, _ in message.answers)

        async with session_factory() as session:
            card = await session.get(Card, card_id)
            reviews = list((await session.scalars(select(ReviewLog))).all())
            leech_events = await session.scalar(
                select(func.count(Event.id)).where(Event.name == "leech_detected")
            )
            assert card.reps == 1
            assert card.review_lapses == 4
            assert card.suspended is True
            assert len(reviews) == 1
            assert reviews[0].request_id == f"tg:{TelegramUser.id}:501"
            assert leech_events == 1

    asyncio.run(check())


def test_ordinary_rating_double_tap_sends_one_next_card_and_records_once(
    session_factory,
    monkeypatch,
) -> None:
    monkeypatch.setattr(study, "async_session", session_factory)
    deck_id, card_id, _ = asyncio.run(_create_study_cards(session_factory))
    message = Message(message_id=502)
    state = State({"study_scope": "deck", "deck_id": deck_id})
    callback = Callback(f"study:rate:{card_id}:3", message)

    async def check() -> None:
        await study.rate_study_card(callback, state)
        await study.rate_study_card(callback, state)

        questions = [text for text, _ in message.answers if "<b>Вопрос</b>" in text]
        assert len(questions) == 1

        async with session_factory() as session:
            card = await session.get(Card, card_id)
            reviews = list((await session.scalars(select(ReviewLog))).all())
            counter_total = await session.scalar(
                select(
                    func.coalesce(
                        func.sum(
                            DailyStudyCounter.new_seen + DailyStudyCounter.reviews_done
                        ),
                        0,
                    )
                )
            )
            review_events = await session.scalar(
                select(func.count(Event.id)).where(Event.name == "review_answer")
            )
            assert card.reps == 1
            assert card.review_lapses == 3
            assert len(reviews) == 1
            assert reviews[0].request_id == f"tg:{TelegramUser.id}:502"
            assert int(counter_total or 0) == 1
            assert review_events == 1

    asyncio.run(check())


def test_guarded_resume_and_later_double_taps_send_one_next_card(
    session_factory,
    monkeypatch,
) -> None:
    monkeypatch.setattr(study, "async_session", session_factory)

    async def check_resume() -> None:
        deck_id, card_id, _ = await _create_study_cards(session_factory)
        async with session_factory() as session:
            card = await session.get(Card, card_id)
            card.review_lapses = 4
            card.suspended = True
            card.leech_suspended_lapses = 4
            await session.commit()

        message = Message(message_id=601)
        callback = Callback(f"leech:resume:{card_id}:4", message)
        state = State({"study_scope": "deck", "deck_id": deck_id})
        await study.resume_leech_study(callback, state)
        await study.resume_leech_study(callback, state)

        questions = [text for text, _ in message.answers if "<b>Вопрос</b>" in text]
        assert len(questions) == 1

    asyncio.run(check_resume())

    async def check_later() -> None:
        async with session_factory() as session:
            user = await get_or_create_user(
                session,
                SimpleNamespace(
                    id=8202,
                    username="later",
                    full_name="Later",
                    language_code="en",
                ),
            )
            deck = await create_deck(session, user, "Later study")
            first_note = await create_basic_note(session, user, deck, "leech", "answer")
            await create_basic_note(session, user, deck, "next", "answer")
            card = (
                await session.scalars(select(Card).where(Card.note_id == first_note.id))
            ).one()
            card.review_lapses = 4
            card.suspended = True
            card.leech_suspended_lapses = 4
            await session.commit()
            card_id = card.id
            deck_id = deck.id

        class LaterTelegramUser:
            id = 8202
            username = "later"
            full_name = "Later"
            language_code = "en"

        message = Message(message_id=602)
        message.chat = SimpleNamespace(id=8202)
        callback = Callback(f"leech:later:{card_id}:4", message)
        callback.from_user = LaterTelegramUser()
        state = State({"study_scope": "deck", "deck_id": deck_id})
        await study.leave_leech_for_later(callback, state)
        await study.leave_leech_for_later(callback, state)

        questions = [text for text, _ in message.answers if "<b>Вопрос</b>" in text]
        assert len(questions) == 1
        async with session_factory() as session:
            card = await session.get(Card, card_id)
            assert card.suspended is True
            assert card.leech_suspended_lapses is None

    asyncio.run(check_later())
