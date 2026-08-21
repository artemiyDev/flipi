import asyncio
import hashlib
import hmac
import json
import time
from datetime import UTC, date, datetime, timedelta
from types import SimpleNamespace
from urllib.parse import urlencode

import httpx
from sqlalchemy import func, select

from app.deps import get_db_session
from app.main import create_app
from bot.models import Card, DailyStudyCounter, Deck, Event, ReviewLog, User
from bot.services.cards import (
    count_due_cards_by_query,
    create_basic_note,
    get_next_due_card,
    get_next_due_card_by_query,
    get_next_new_card_without_limit,
    get_next_review_ahead_card,
)
from bot.services.decks import archive_deck, create_deck, get_deck_counts
from bot.services.study import get_next_card_for_user
from bot.services.users import get_or_create_user

NOW = datetime(2026, 8, 21, 16, 0, tzinfo=UTC)
TEST_BOT_TOKEN = "test-bot-token"
TEST_TELEGRAM_ID = 26001


class TelegramUser:
    id = TEST_TELEGRAM_ID
    username = "learn-ahead"
    full_name = "Learn Ahead"
    language_code = "en"


class ForeignTelegramUser:
    id = TEST_TELEGRAM_ID + 1
    username = "foreign"
    full_name = "Foreign"
    language_code = "en"


async def _make_card(
    session,
    user: User,
    deck: Deck,
    front: str,
    state: str,
    due_at: datetime,
    *,
    tags: list[str] | None = None,
) -> Card:
    note = await create_basic_note(session, user, deck, front, "answer", tags=tags)
    card = (
        await session.scalars(select(Card).where(Card.note_id == note.id))
    ).one()
    card.state = state
    card.due_at = due_at
    await session.flush()
    return card


def test_deck_queue_is_due_first_and_uses_inclusive_twenty_minute_window(
    session_factory,
) -> None:
    async def check() -> None:
        async with session_factory() as session:
            user = await get_or_create_user(session, TelegramUser())
            deck = await create_deck(session, user, "Boundaries")
            ahead = await _make_card(
                session, user, deck, "ahead", "learning", NOW + timedelta(minutes=5)
            )
            exact = await _make_card(
                session, user, deck, "exact", "relearning", NOW + timedelta(minutes=20)
            )
            outside = await _make_card(
                session,
                user,
                deck,
                "outside",
                "learning",
                NOW + timedelta(minutes=20, microseconds=1),
            )
            future_new = await _make_card(
                session, user, deck, "future-new", "new", NOW + timedelta(minutes=1)
            )
            future_review = await _make_card(
                session, user, deck, "future-review", "review", NOW + timedelta(seconds=1)
            )
            due_new = await _make_card(
                session, user, deck, "due", "new", NOW
            )
            await session.commit()

            selected = await get_next_due_card(session, deck, user.timezone, NOW)
            assert selected.id == due_new.id

            due_new.suspended = True
            await session.commit()
            selected = await get_next_due_card(session, deck, user.timezone, NOW)
            assert selected.id == ahead.id

            ahead.suspended = True
            await session.commit()
            selected = await get_next_due_card(session, deck, user.timezone, NOW)
            assert selected.id == exact.id

            exact.suspended = True
            await session.commit()
            assert await get_next_due_card(session, deck, user.timezone, NOW) is None
            assert outside.id != future_new.id != future_review.id

    asyncio.run(check())


def test_all_scope_checks_every_deck_due_now_then_uses_global_due_and_id_order(
    session_factory,
) -> None:
    async def check() -> None:
        async with session_factory() as session:
            user = await get_or_create_user(session, TelegramUser())
            alpha = await create_deck(session, user, "Alpha")
            beta = await create_deck(session, user, "Beta")
            alpha_ahead = await _make_card(
                session, user, alpha, "alpha-ahead", "learning", NOW + timedelta(minutes=1)
            )
            beta_due = await _make_card(session, user, beta, "beta-due", "new", NOW)
            beta_ahead = await _make_card(
                session, user, beta, "beta-ahead", "relearning", NOW + timedelta(minutes=2)
            )
            await session.commit()

            selected = await get_next_card_for_user(session, user, NOW)
            assert selected.id == beta_due.id

            beta_due.suspended = True
            await session.commit()
            selected = await get_next_card_for_user(session, user, NOW)
            assert selected.id == alpha_ahead.id

            tie_due = NOW + timedelta(minutes=3)
            alpha_ahead.due_at = tie_due
            beta_ahead.due_at = tie_due
            await session.commit()
            selected = await get_next_card_for_user(session, user, NOW)
            assert selected.id == min(alpha_ahead.id, beta_ahead.id)

    asyncio.run(check())


def test_standard_queue_excludes_inactive_buried_archived_and_foreign_cards(
    session_factory,
) -> None:
    async def check() -> None:
        async with session_factory() as session:
            user = await get_or_create_user(session, TelegramUser())
            foreign = await get_or_create_user(session, ForeignTelegramUser())
            deck = await create_deck(session, user, "Scoped")
            archived = await create_deck(session, user, "Archived")
            foreign_deck = await create_deck(session, foreign, "Foreign")

            suspended = await _make_card(
                session, user, deck, "suspended", "learning", NOW + timedelta(minutes=2)
            )
            suspended.suspended = True
            buried = await _make_card(
                session, user, deck, "buried", "relearning", NOW + timedelta(minutes=3)
            )
            buried.buried_until = date(2026, 8, 22)
            archived_card = await _make_card(
                session, user, archived, "archived", "learning", NOW + timedelta(minutes=1)
            )
            foreign_card = await _make_card(
                session,
                foreign,
                foreign_deck,
                "foreign",
                "learning",
                NOW + timedelta(seconds=1),
            )
            await session.commit()
            await archive_deck(session, archived)

            assert await get_next_due_card(session, deck, user.timezone, NOW) is None
            assert await get_next_due_card(session, archived, user.timezone, NOW) is None
            assert await get_next_card_for_user(session, user, NOW) is None
            assert archived_card.user_id == user.id
            assert foreign_card.user_id != user.id

    asyncio.run(check())


def test_filtered_queue_keeps_query_scope_and_is_due_remains_strict(
    session_factory,
) -> None:
    async def check() -> None:
        async with session_factory() as session:
            user = await get_or_create_user(session, TelegramUser())
            deck = await create_deck(session, user, "Filtered")
            due = await _make_card(
                session, user, deck, "target due", "new", NOW, tags=["target"]
            )
            ahead = await _make_card(
                session,
                user,
                deck,
                "target ahead",
                "learning",
                NOW + timedelta(minutes=7),
                tags=["target"],
            )
            other = await _make_card(
                session,
                user,
                deck,
                "other ahead",
                "learning",
                NOW + timedelta(minutes=1),
                tags=["other"],
            )
            await session.commit()

            selected = await get_next_due_card_by_query(session, user, "tag:target", NOW)
            assert selected.id == due.id

            due.suspended = True
            await session.commit()
            selected = await get_next_due_card_by_query(session, user, "tag:target", NOW)
            assert selected.id == ahead.id
            assert selected.id != other.id
            assert await count_due_cards_by_query(session, user, "tag:target", NOW) == 1
            assert await get_next_due_card_by_query(
                session, user, "tag:target is:due", NOW
            ) is None
            assert await count_due_cards_by_query(
                session, user, "tag:target is:due", NOW
            ) == 0

    asyncio.run(check())


def test_custom_study_selectors_keep_their_explicit_behavior(session_factory) -> None:
    async def check() -> None:
        async with session_factory() as session:
            user = await get_or_create_user(session, TelegramUser())
            deck = await create_deck(session, user, "Custom")
            deck.new_cards_per_day = 0
            future_review = await _make_card(
                session, user, deck, "review ahead", "review", datetime.now(UTC) + timedelta(days=2)
            )
            due_new = await _make_card(
                session, user, deck, "new unlimited", "new", datetime.now(UTC) - timedelta(seconds=1)
            )
            await session.commit()

            assert await get_next_due_card(session, deck, user.timezone) is None
            assert (
                await get_next_review_ahead_card(session, deck, user.timezone)
            ).id == future_review.id
            assert (
                await get_next_new_card_without_limit(session, deck, user.timezone)
            ).id == due_new.id

    asyncio.run(check())


def test_daily_limit_lookup_uses_the_queue_clock_local_date(session_factory) -> None:
    async def check() -> None:
        async with session_factory() as session:
            user = await get_or_create_user(session, TelegramUser())
            user.timezone = "America/New_York"
            deck = await create_deck(session, user, "Midnight")
            deck.new_cards_per_day = 1
            card = await _make_card(
                session,
                user,
                deck,
                "due new",
                "new",
                datetime(2026, 8, 21, 3, 0, tzinfo=UTC),
            )
            session.add_all(
                [
                    DailyStudyCounter(
                        user_id=user.id,
                        deck_id=deck.id,
                        study_date=date(2026, 8, 20),
                        new_seen=0,
                        reviews_done=0,
                    ),
                    DailyStudyCounter(
                        user_id=user.id,
                        deck_id=deck.id,
                        study_date=date(2026, 8, 21),
                        new_seen=1,
                        reviews_done=0,
                    ),
                ]
            )
            await session.commit()

            before_midnight = datetime(2026, 8, 21, 3, 30, tzinfo=UTC)
            after_midnight = datetime(2026, 8, 21, 4, 30, tzinfo=UTC)
            assert (
                await get_next_due_card(session, deck, user.timezone, before_midnight)
            ).id == card.id
            assert await get_next_due_card(
                session, deck, user.timezone, after_midnight
            ) is None

    asyncio.run(check())


def test_deck_counts_include_only_available_learning_inside_window(session_factory) -> None:
    async def check() -> None:
        async with session_factory() as session:
            user = await get_or_create_user(session, TelegramUser())
            deck = await create_deck(session, user, "Counts")
            await _make_card(session, user, deck, "new", "new", NOW)
            await _make_card(session, user, deck, "review", "review", NOW)
            await _make_card(
                session, user, deck, "learn exact", "learning", NOW + timedelta(minutes=20)
            )
            await _make_card(
                session,
                user,
                deck,
                "learn outside",
                "learning",
                NOW + timedelta(minutes=20, seconds=1),
            )
            future_new = await _make_card(
                session, user, deck, "future new", "new", NOW + timedelta(minutes=1)
            )
            future_review = await _make_card(
                session, user, deck, "future review", "review", NOW + timedelta(minutes=1)
            )
            suspended = await _make_card(
                session, user, deck, "suspended", "relearning", NOW + timedelta(minutes=1)
            )
            suspended.suspended = True
            buried = await _make_card(
                session, user, deck, "buried", "learning", NOW + timedelta(minutes=1)
            )
            buried.buried_until = date(2026, 8, 22)
            await session.commit()

            assert await get_deck_counts(session, deck, user.timezone, NOW) == (1, 1, 1)
            assert future_new.state == "new"
            assert future_review.state == "review"

    asyncio.run(check())


def _signed_init_data() -> str:
    data = {
        "auth_date": str(int(time.time())),
        "query_id": "learn-ahead-query",
        "user": json.dumps(
            {
                "id": TEST_TELEGRAM_ID,
                "first_name": "Learn",
                "last_name": "Ahead",
                "username": "learn-ahead",
                "language_code": "en",
            },
            separators=(",", ":"),
        ),
    }
    check = "\n".join(f"{key}={value}" for key, value in sorted(data.items()))
    secret = hmac.new(b"WebAppData", TEST_BOT_TOKEN.encode(), hashlib.sha256).digest()
    data["hash"] = hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()
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


def _request(app, method: str, path: str, payload: dict | None = None) -> httpx.Response:
    async def send() -> httpx.Response:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            return await client.request(
                method,
                path,
                json=payload,
                headers={"X-Telegram-Init-Data": _signed_init_data()},
            )

    return asyncio.run(send())


def test_study_next_returns_exact_nullable_learn_ahead_and_one_clock_snapshot(
    session_factory,
    monkeypatch,
) -> None:
    import app.api as api_module

    class FixedDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            return NOW if tz is not None else NOW.replace(tzinfo=None)

    monkeypatch.setattr(api_module, "datetime", FixedDateTime)

    async def create_data() -> tuple[int, int, int]:
        async with session_factory() as session:
            user = await get_or_create_user(session, TelegramUser())
            deck = await create_deck(session, user, "API")
            ordinary = await _make_card(session, user, deck, "ordinary", "new", NOW)
            ahead = await _make_card(
                session,
                user,
                deck,
                "ahead",
                "learning",
                NOW + timedelta(minutes=9, microseconds=200_000),
            )
            await session.commit()
            return deck.id, ordinary.id, ahead.id

    deck_id, ordinary_id, ahead_id = asyncio.run(create_data())
    app = _build_app(session_factory, monkeypatch)

    ordinary = _request(app, "GET", f"/api/study/next?deck_id={deck_id}")
    assert ordinary.status_code == 200
    assert ordinary.json()["card_id"] == ordinary_id
    assert ordinary.json()["learn_ahead"] is None

    async def suspend_ordinary() -> None:
        async with session_factory() as session:
            card = await session.get(Card, ordinary_id)
            card.suspended = True
            await session.commit()

    asyncio.run(suspend_ordinary())
    early = _request(app, "GET", f"/api/study/next?deck_id={deck_id}")
    assert early.status_code == 200
    assert early.json()["card_id"] == ahead_id
    assert early.json()["learn_ahead"] == {
        "scheduled_for": "2026-08-21T16:09:00.200000Z",
        "seconds_early": 541,
    }
    assert early.json()["progress"] == {"new": 0, "learning": 1, "review": 0}


def test_study_next_passes_one_snapshot_to_selector_goals_progress_and_done(
    session_factory,
    monkeypatch,
) -> None:
    import app.api as api_module

    snapshots: list[tuple[str, datetime]] = []
    first_now = NOW
    second_now = NOW + timedelta(seconds=1)

    class TickingDateTime(datetime):
        calls = 0

        @classmethod
        def now(cls, tz=None):
            value = first_now if cls.calls == 0 else second_now
            cls.calls += 1
            return value if tz is not None else value.replace(tzinfo=None)

    original_selector = api_module.get_next_card_for_user
    original_goals = api_module.daily_goal_progress
    original_counts = api_module.get_deck_counts
    original_done = api_module.count_done_today

    async def selector(session, user, now_utc):
        snapshots.append(("selector", now_utc))
        return await original_selector(session, user, now_utc)

    async def goals(session, user, now_utc):
        snapshots.append(("goals", now_utc))
        return await original_goals(session, user, now_utc)

    async def counts(session, deck, timezone_name, now_utc):
        snapshots.append(("counts", now_utc))
        return await original_counts(session, deck, timezone_name, now_utc)

    async def done(session, user, now_utc):
        snapshots.append(("done", now_utc))
        return await original_done(session, user, now_utc)

    monkeypatch.setattr(api_module, "datetime", TickingDateTime)
    monkeypatch.setattr(api_module, "get_next_card_for_user", selector)
    monkeypatch.setattr(api_module, "daily_goal_progress", goals)
    monkeypatch.setattr(api_module, "get_deck_counts", counts)
    monkeypatch.setattr(api_module, "count_done_today", done)

    async def create_data() -> int:
        async with session_factory() as session:
            user = await get_or_create_user(session, TelegramUser())
            deck = await create_deck(session, user, "Clock")
            card = await _make_card(session, user, deck, "due", "new", first_now)
            await session.commit()
            return card.id

    card_id = asyncio.run(create_data())
    app = _build_app(session_factory, monkeypatch)
    first = _request(app, "GET", "/api/study/next?deck_id=all")
    assert first.json()["card_id"] == card_id
    assert snapshots == [
        ("selector", first_now),
        ("goals", first_now),
        ("counts", first_now),
    ]

    async def suspend_card() -> None:
        async with session_factory() as session:
            card = await session.get(Card, card_id)
            card.suspended = True
            await session.commit()

    asyncio.run(suspend_card())
    snapshots.clear()
    second = _request(app, "GET", "/api/study/next?deck_id=all")
    assert second.json()["card_id"] is None
    assert snapshots == [
        ("selector", second_now),
        ("goals", second_now),
        ("done", second_now),
    ]
    assert TickingDateTime.calls == 2


def test_early_answer_replay_records_one_transition_and_keeps_leech_semantics(
    session_factory,
    monkeypatch,
) -> None:
    now = datetime.now(UTC).replace(microsecond=0)

    async def create_data() -> tuple[int, int]:
        async with session_factory() as session:
            user = await get_or_create_user(session, TelegramUser())
            deck = await create_deck(session, user, "Replay")
            card = await _make_card(
                session, user, deck, "early", "learning", now + timedelta(minutes=5)
            )
            card.review_lapses = 3
            await session.commit()
            return deck.id, card.id

    deck_id, card_id = asyncio.run(create_data())
    app = _build_app(session_factory, monkeypatch)
    selected = _request(app, "GET", f"/api/study/next?deck_id={deck_id}")
    assert selected.json()["card_id"] == card_id
    assert selected.json()["learn_ahead"]["seconds_early"] > 0

    payload = {
        "card_id": card_id,
        "rating": 1,
        "elapsed_ms": 42,
        "request_id": "learn-ahead-replay",
    }
    first = _request(app, "POST", "/api/study/answer", payload)
    replay = _request(app, "POST", "/api/study/answer", payload)
    assert first.json()["replayed"] is False
    assert replay.json()["replayed"] is True
    assert first.json()["leech"] is None
    assert replay.json()["leech"] is None

    async def inspect() -> tuple[int, int, int, int, int, bool]:
        async with session_factory() as session:
            card = await session.get(Card, card_id)
            reviews = await session.scalar(select(func.count(ReviewLog.id)))
            counters = await session.scalar(
                select(
                    func.coalesce(
                        func.sum(DailyStudyCounter.new_seen + DailyStudyCounter.reviews_done),
                        0,
                    )
                )
            )
            events = await session.scalar(
                select(func.count(Event.id)).where(Event.name == "review_answer")
            )
            return (
                int(reviews or 0),
                int(counters or 0),
                int(events or 0),
                card.reps,
                card.review_lapses,
                card.suspended,
            )

    assert asyncio.run(inspect()) == (1, 1, 1, 1, 3, False)
