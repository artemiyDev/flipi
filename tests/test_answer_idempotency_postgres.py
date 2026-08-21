import asyncio
import os
import re
from collections.abc import Iterator
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import func, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.deps import get_current_user, get_db_session
from app.main import create_app
from bot.config import get_settings
from bot.models import Card, DailyStudyCounter, Event, ReviewLog
from bot.services.cards import create_basic_note
from bot.services.decks import create_deck
from bot.services.users import get_or_create_user

TEST_DATABASE_ENV = "FLIPI_TEST_DATABASE_URL"
TEST_DATABASE_NAME_RE = re.compile(r"(^|[-_])test([-_]|$)", re.IGNORECASE)
ROOT = Path(__file__).resolve().parents[1]
TEST_TELEGRAM_ID = 923456789


def _database_target() -> tuple[str, str]:
    raw_url = os.environ.get(TEST_DATABASE_ENV)
    if not raw_url:
        pytest.skip(f"Set {TEST_DATABASE_ENV} to a dedicated PostgreSQL test database")

    url = make_url(raw_url)
    database_name = url.database or ""
    if url.drivername != "postgresql+asyncpg":
        pytest.fail(
            f"{TEST_DATABASE_ENV} must use the postgresql+asyncpg driver",
            pytrace=False,
        )
    if not TEST_DATABASE_NAME_RE.search(database_name):
        pytest.fail(
            f"{TEST_DATABASE_ENV} database name must contain a separate 'test' segment",
            pytrace=False,
        )
    return url.render_as_string(hide_password=False), database_name


async def _recreate_public_schema(database_url: str, expected_database: str) -> None:
    engine = create_async_engine(database_url, poolclass=NullPool)
    try:
        async with engine.begin() as connection:
            current_database = await connection.scalar(text("SELECT current_database()"))
            if current_database != expected_database:
                raise RuntimeError("Connected database does not match the guarded test target")
            await connection.execute(text("DROP SCHEMA IF EXISTS public CASCADE"))
            await connection.execute(text("CREATE SCHEMA public"))
    finally:
        await engine.dispose()


@pytest.fixture
def postgres_session_factory(
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[async_sessionmaker[AsyncSession]]:
    database_url, database_name = _database_target()
    asyncio.run(_recreate_public_schema(database_url, database_name))

    monkeypatch.setenv("DATABASE_URL", database_url)
    get_settings.cache_clear()
    alembic_config = Config(str(ROOT / "alembic.ini"))
    alembic_config.set_main_option("script_location", str(ROOT / "migrations"))
    command.upgrade(alembic_config, "head")

    engine = create_async_engine(database_url, poolclass=NullPool)
    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    try:
        yield factory
    finally:
        asyncio.run(engine.dispose())
        get_settings.cache_clear()


async def _create_cards(
    session_factory: async_sessionmaker[AsyncSession],
    count: int,
) -> tuple[int, list[int]]:
    async with session_factory() as session:
        user = await get_or_create_user(
            session,
            SimpleNamespace(
                id=TEST_TELEGRAM_ID,
                username="postgres_concurrency",
                full_name="PostgreSQL Concurrency",
                language_code="en",
            ),
        )
        deck = await create_deck(session, user, "Concurrent study")
        for index in range(count):
            await create_basic_note(
                session,
                user,
                deck,
                f"question {index}",
                f"answer {index}",
            )
        card_ids = list(
            (
                await session.scalars(
                    select(Card.id).where(Card.user_id == user.id).order_by(Card.id)
                )
            ).all()
        )
        return user.id, card_ids


async def _post_concurrently(
    session_factory: async_sessionmaker[AsyncSession],
    user_id: int,
    payloads: tuple[dict, dict],
) -> tuple[httpx.Response, httpx.Response]:
    arrived = 0
    arrival_lock = asyncio.Lock()
    both_ready = asyncio.Event()

    async def override_db_session():
        nonlocal arrived
        async with session_factory() as session:
            async with arrival_lock:
                arrived += 1
                if arrived == 2:
                    both_ready.set()
            await asyncio.wait_for(both_ready.wait(), timeout=10)
            yield session

    async def override_current_user():
        return SimpleNamespace(id=user_id, timezone="UTC")

    app = create_app()
    app.dependency_overrides[get_db_session] = override_db_session
    app.dependency_overrides[get_current_user] = override_current_user
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        first, second = await asyncio.gather(
            client.post("/api/study/answer", json=payloads[0]),
            client.post("/api/study/answer", json=payloads[1]),
        )
    return first, second


async def _side_effects(
    session_factory: async_sessionmaker[AsyncSession],
) -> tuple[list[Card], list[ReviewLog], int, int]:
    async with session_factory() as session:
        cards = list((await session.scalars(select(Card).order_by(Card.id))).all())
        reviews = list((await session.scalars(select(ReviewLog).order_by(ReviewLog.id))).all())
        counter_total = await session.scalar(
            select(
                func.coalesce(
                    func.sum(DailyStudyCounter.new_seen + DailyStudyCounter.reviews_done),
                    0,
                )
            )
        )
        event_count = await session.scalar(
            select(func.count(Event.id)).where(Event.name == "review_answer")
        )
        return cards, reviews, int(counter_total or 0), int(event_count or 0)


def test_concurrent_identical_answers_are_applied_once(
    postgres_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    user_id, card_ids = asyncio.run(_create_cards(postgres_session_factory, 1))
    payload = {
        "card_id": card_ids[0],
        "rating": 1,
        "elapsed_ms": 321,
        "request_id": "concurrent-same-card-key",
    }

    responses = asyncio.run(
        _post_concurrently(postgres_session_factory, user_id, (payload, payload))
    )

    assert [response.status_code for response in responses] == [200, 200]
    bodies = [response.json() for response in responses]
    assert sorted(body["replayed"] for body in bodies) == [False, True]
    assert {body["state"] for body in bodies} == {bodies[0]["state"]}
    assert {body["due"] for body in bodies} == {bodies[0]["due"]}

    cards, reviews, counter_total, event_count = asyncio.run(
        _side_effects(postgres_session_factory)
    )
    assert len(cards) == 1
    assert cards[0].reps == 1
    assert cards[0].lapses == 1
    assert len(reviews) == 1
    assert reviews[0].request_id == payload["request_id"]
    assert counter_total == 1
    assert event_count == 1


def test_concurrent_key_reuse_for_different_cards_conflicts_without_extra_mutation(
    postgres_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    user_id, card_ids = asyncio.run(_create_cards(postgres_session_factory, 2))
    payloads = (
        {
            "card_id": card_ids[0],
            "rating": 3,
            "request_id": "concurrent-cross-card-key",
        },
        {
            "card_id": card_ids[1],
            "rating": 3,
            "request_id": "concurrent-cross-card-key",
        },
    )

    responses = asyncio.run(
        _post_concurrently(postgres_session_factory, user_id, payloads)
    )

    assert sorted(response.status_code for response in responses) == [200, 409]
    winner_index = next(
        index for index, response in enumerate(responses) if response.status_code == 200
    )
    winner_card_id = payloads[winner_index]["card_id"]

    cards, reviews, counter_total, event_count = asyncio.run(
        _side_effects(postgres_session_factory)
    )
    assert sum(card.reps for card in cards) == 1
    assert sorted(card.reps for card in cards) == [0, 1]
    assert len(reviews) == 1
    assert reviews[0].card_id == winner_card_id
    assert reviews[0].request_id == payloads[0]["request_id"]
    assert counter_total == 1
    assert event_count == 1
