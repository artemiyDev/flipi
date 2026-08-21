import asyncio
import hashlib
import hmac
import json
import time
from types import SimpleNamespace
from urllib.parse import urlencode

import httpx
from sqlalchemy import func, select

from app.deps import get_db_session
from app.main import create_app
from bot.models import Card, DailyStudyCounter, Event, ReviewLog
from bot.services.cards import create_basic_note
from bot.services.decks import create_deck
from bot.services.users import get_or_create_user

TEST_BOT_TOKEN = "test-bot-token"
TEST_TELEGRAM_ID = 123456789


def _signed_init_data(telegram_id: int = TEST_TELEGRAM_ID) -> str:
    data = {
        "auth_date": str(int(time.time())),
        "query_id": "test-query",
        "user": json.dumps(
            {
                "id": telegram_id,
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


def _build_app(session_factory, monkeypatch):
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


def _post(app, payload: dict, telegram_id: int = TEST_TELEGRAM_ID) -> httpx.Response:
    async def send() -> httpx.Response:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            return await client.post(
                "/api/study/answer",
                json=payload,
                headers={"X-Telegram-Init-Data": _signed_init_data(telegram_id)},
            )

    return asyncio.run(send())


async def _create_cards(
    session_factory,
    count: int = 1,
    telegram_id: int = TEST_TELEGRAM_ID,
) -> list[int]:
    async with session_factory() as session:
        user = await get_or_create_user(
            session,
            SimpleNamespace(
                id=telegram_id,
                username=f"user{telegram_id}",
                full_name="Test User",
                language_code="en",
            ),
        )
        deck = await create_deck(session, user, "Study")
        for index in range(count):
            await create_basic_note(session, user, deck, f"question {index}", f"answer {index}")
        return list(
            (
                await session.scalars(
                    select(Card.id).where(Card.user_id == user.id).order_by(Card.id)
                )
            ).all()
        )


def test_repeated_request_replays_saved_answer_without_side_effects(
    session_factory, monkeypatch
) -> None:
    card_id = asyncio.run(_create_cards(session_factory))[0]
    app = _build_app(session_factory, monkeypatch)
    payload = {
        "card_id": card_id,
        "rating": 1,
        "elapsed_ms": 321,
        "request_id": "043132f2-b75d-499f-9a39-7e9f64e395d1",
    }

    first = _post(app, payload)
    second = _post(app, {**payload, "elapsed_ms": 999})

    assert first.status_code == 200
    assert second.status_code == 200
    assert first.json()["replayed"] is False
    assert second.json()["replayed"] is True
    assert second.json()["state"] == first.json()["state"]
    assert second.json()["due"] == first.json()["due"]

    async def inspect() -> tuple[Card, list[ReviewLog], int, int]:
        async with session_factory() as session:
            card = await session.get(Card, card_id)
            reviews = list((await session.scalars(select(ReviewLog))).all())
            counter_total = (
                await session.scalar(
                    select(
                        func.coalesce(
                            func.sum(
                                DailyStudyCounter.new_seen
                                + DailyStudyCounter.reviews_done
                            ),
                            0,
                        )
                    )
                )
            )
            event_count = await session.scalar(
                select(func.count(Event.id)).where(Event.name == "review_answer")
            )
            return card, reviews, int(counter_total or 0), int(event_count or 0)

    card, reviews, counter_total, event_count = asyncio.run(inspect())
    assert card.reps == 1
    assert card.lapses == 1
    assert len(reviews) == 1
    assert reviews[0].request_id == payload["request_id"]
    assert reviews[0].state_after == first.json()["state"]
    assert reviews[0].elapsed_ms == 321
    assert counter_total == 1
    assert event_count == 1


def test_request_id_conflict_does_not_mutate_any_card(session_factory, monkeypatch) -> None:
    first_card_id, second_card_id = asyncio.run(_create_cards(session_factory, count=2))
    app = _build_app(session_factory, monkeypatch)
    request_id = "answer-key-1"

    original = _post(
        app,
        {"card_id": first_card_id, "rating": 3, "request_id": request_id},
    )
    changed_rating = _post(
        app,
        {"card_id": first_card_id, "rating": 4, "request_id": request_id},
    )
    changed_card = _post(
        app,
        {"card_id": second_card_id, "rating": 3, "request_id": request_id},
    )

    assert original.status_code == 200
    assert changed_rating.status_code == 409
    assert changed_card.status_code == 409

    async def inspect() -> tuple[list[int], int, int, int]:
        async with session_factory() as session:
            reps = list(
                (await session.scalars(select(Card.reps).order_by(Card.id))).all()
            )
            review_count = await session.scalar(select(func.count(ReviewLog.id)))
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
            event_count = await session.scalar(
                select(func.count(Event.id)).where(Event.name == "review_answer")
            )
            return (
                reps,
                int(review_count or 0),
                int(counter_total or 0),
                int(event_count or 0),
            )

    assert asyncio.run(inspect()) == ([1, 0], 1, 1, 1)


def test_reused_request_id_does_not_expose_foreign_card_and_is_scoped_per_user(
    session_factory, monkeypatch
) -> None:
    own_card_id = asyncio.run(_create_cards(session_factory))[0]
    foreign_card_id = asyncio.run(
        _create_cards(session_factory, telegram_id=987654)
    )[0]
    app = _build_app(session_factory, monkeypatch)
    request_id = "shared-across-users"

    original = _post(
        app,
        {"card_id": own_card_id, "rating": 3, "request_id": request_id},
    )
    hidden = _post(
        app,
        {"card_id": foreign_card_id, "rating": 3, "request_id": request_id},
    )

    assert original.status_code == 200
    assert hidden.status_code == 404

    async def inspect_foreign_before_answer() -> tuple[int, int]:
        async with session_factory() as session:
            card = await session.get(Card, foreign_card_id)
            review_count = await session.scalar(select(func.count(ReviewLog.id)))
            return card.reps, int(review_count or 0)

    assert asyncio.run(inspect_foreign_before_answer()) == (0, 1)

    allowed = _post(
        app,
        {"card_id": foreign_card_id, "rating": 3, "request_id": request_id},
        telegram_id=987654,
    )

    assert allowed.status_code == 200
    assert allowed.json()["replayed"] is False

    async def inspect_after_answer() -> tuple[list[int], int]:
        async with session_factory() as session:
            reps = list(
                (await session.scalars(select(Card.reps).order_by(Card.id))).all()
            )
            review_count = await session.scalar(select(func.count(ReviewLog.id)))
            return reps, int(review_count or 0)

    assert asyncio.run(inspect_after_answer()) == ([1, 1], 2)


def test_answer_without_request_id_and_foreign_card_keep_existing_behavior(
    session_factory, monkeypatch
) -> None:
    card_id = asyncio.run(_create_cards(session_factory))[0]
    app = _build_app(session_factory, monkeypatch)

    response = _post(app, {"card_id": card_id, "rating": 3})
    foreign = _post(app, {"card_id": card_id, "rating": 3}, telegram_id=987654)
    invalid_key = _post(
        app,
        {"card_id": card_id, "rating": 3, "request_id": "contains whitespace"},
    )

    assert response.status_code == 200
    assert response.json()["ok"] is True
    assert response.json()["replayed"] is False
    assert foreign.status_code == 404
    assert invalid_key.status_code == 422
