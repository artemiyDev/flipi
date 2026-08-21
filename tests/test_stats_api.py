import asyncio
import hashlib
import hmac
import json
import time
from datetime import UTC, datetime, time as datetime_time, timedelta
from types import SimpleNamespace
from urllib.parse import urlencode
from zoneinfo import ZoneInfo

import httpx
import pytest
from sqlalchemy import select

from app.deps import get_db_session
from app.main import create_app
from bot.models import Card, ReviewLog, User
from bot.services.cards import create_basic_note
from bot.services.decks import archive_deck, create_deck
from bot.services.timezones import user_today
from bot.services.users import get_or_create_user

TEST_BOT_TOKEN = "test-bot-token"
TEST_TELEGRAM_ID = 123456789


class TelegramUser:
    id = TEST_TELEGRAM_ID
    username = "testuser"
    full_name = "Test User"
    language_code = "en"


def signed_init_data() -> str:
    data = {
        "auth_date": str(int(time.time())),
        "query_id": "stats-test-query",
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


def request(app, path: str, authorized: bool = True) -> httpx.Response:
    async def send() -> httpx.Response:
        transport = httpx.ASGITransport(app=app)
        headers = {"X-Telegram-Init-Data": signed_init_data()} if authorized else {}
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            return await client.get(path, headers=headers)

    return asyncio.run(send())


def local_datetime(day, timezone_name: str, hour: int = 12, minute: int = 0) -> datetime:
    return datetime.combine(
        day, datetime_time(hour, minute), tzinfo=ZoneInfo(timezone_name)
    ).astimezone(UTC)


async def create_card(session, user: User, deck, name: str) -> Card:
    note = await create_basic_note(session, user, deck, name, name)
    result = await session.execute(select(Card).where(Card.note_id == note.id))
    return result.scalar_one()


def review_log(card: Card, reviewed_at: datetime, rating: int) -> ReviewLog:
    return ReviewLog(
        user_id=card.user_id,
        deck_id=card.deck_id,
        card_id=card.id,
        rating=rating,
        reviewed_at=reviewed_at,
        elapsed_ms=100,
        previous_due_at=reviewed_at,
        next_due_at=reviewed_at + timedelta(days=1),
    )


def test_stats_endpoints_require_init_data(session_factory, monkeypatch) -> None:
    app = build_app(session_factory, monkeypatch)

    for path in ("/api/stats/overview", "/api/stats/heatmap", "/api/stats/forecast"):
        assert request(app, path, authorized=False).status_code == 401


def test_overview_returns_counts_retention_and_due_now(session_factory, monkeypatch) -> None:
    timezone_name = "America/Sao_Paulo"

    async def create_data() -> None:
        async with session_factory() as session:
            user = await get_or_create_user(session, TelegramUser())
            user.timezone = timezone_name
            deck = await create_deck(session, user, "Active")
            archived_deck = await create_deck(session, user, "Archived")
            cards = [await create_card(session, user, deck, f"card-{index}") for index in range(3)]
            archived_card = await create_card(session, user, archived_deck, "archived")
            cards[1].state = "learning"
            cards[2].state = "review"
            archived_card.state = "review"
            await archive_deck(session, archived_deck)
            today = user_today(timezone_name)
            session.add_all(
                [
                    review_log(cards[0], local_datetime(today, timezone_name, 10), 3),
                    review_log(cards[0], local_datetime(today, timezone_name, 11), 1),
                    review_log(cards[0], local_datetime(today - timedelta(days=5), timezone_name), 2),
                    review_log(cards[0], local_datetime(today - timedelta(days=30), timezone_name), 4),
                ]
            )
            await session.commit()

    asyncio.run(create_data())
    response = request(build_app(session_factory, monkeypatch), "/api/stats/overview")

    assert response.status_code == 200
    payload = response.json()
    assert payload["due_now"] == 3
    assert payload["done_today"] == 2
    assert payload["retention_30d"] == pytest.approx(2 / 3)
    assert payload["ratings_30d"] == {"again": 1, "hard": 1, "good": 1, "easy": 0}


def test_overview_empty_history_has_null_retention(session_factory, monkeypatch) -> None:
    response = request(build_app(session_factory, monkeypatch), "/api/stats/overview")

    assert response.status_code == 200
    assert response.json() == {
        "due_now": 0,
        "done_today": 0,
        "streak_days": 0,
        "retention_30d": None,
        "ratings_30d": {"again": 0, "hard": 0, "good": 0, "easy": 0},
    }


@pytest.mark.parametrize(
    ("review_offsets", "expected_streak"),
    [([0, -1, -2], 3), ([-1, -2], 2), ([0, -1, -3], 2)],
)
def test_overview_calculates_continuous_streak(
    session_factory,
    monkeypatch,
    review_offsets: list[int],
    expected_streak: int,
) -> None:
    async def create_data() -> None:
        async with session_factory() as session:
            user = await get_or_create_user(session, TelegramUser())
            deck = await create_deck(session, user, "Streak")
            card = await create_card(session, user, deck, "card")
            today = user_today(user.timezone)
            session.add_all(
                [
                    review_log(
                        card,
                        local_datetime(
                            today + timedelta(days=offset),
                            user.timezone,
                            12,
                            minute,
                        ),
                        3,
                    )
                    for offset in review_offsets
                    for minute in range(10)
                ]
            )
            await session.commit()

    asyncio.run(create_data())
    payload = request(build_app(session_factory, monkeypatch), "/api/stats/overview").json()

    assert payload["streak_days"] == expected_streak


def test_heatmap_uses_local_dates_and_validates_weeks(session_factory, monkeypatch) -> None:
    timezone_name = "America/Los_Angeles"

    async def create_data() -> tuple[str, str]:
        async with session_factory() as session:
            user = await get_or_create_user(session, TelegramUser())
            user.timezone = timezone_name
            deck = await create_deck(session, user, "Heatmap")
            card = await create_card(session, user, deck, "card")
            today = user_today(timezone_name)
            yesterday = today - timedelta(days=1)
            session.add_all(
                [
                    review_log(card, local_datetime(yesterday, timezone_name, 23, 30), 3),
                    review_log(card, local_datetime(today, timezone_name, 10), 4),
                    review_log(card, local_datetime(today, timezone_name, 11), 2),
                ]
            )
            await session.commit()
            return yesterday.isoformat(), today.isoformat()

    yesterday, today = asyncio.run(create_data())
    app = build_app(session_factory, monkeypatch)
    response = request(app, "/api/stats/heatmap?weeks=2")

    assert response.status_code == 200
    assert response.json() == {
        "days": [{"date": yesterday, "count": 1}, {"date": today, "count": 2}]
    }
    assert request(app, "/api/stats/heatmap?weeks=0").status_code == 422
    assert request(app, "/api/stats/heatmap?weeks=54").status_code == 422


def test_forecast_separates_overdue_and_filters_inactive_cards(session_factory, monkeypatch) -> None:
    timezone_name = "America/Sao_Paulo"

    async def create_data() -> tuple[str, str]:
        async with session_factory() as session:
            user = await get_or_create_user(session, TelegramUser())
            user.timezone = timezone_name
            deck = await create_deck(session, user, "Forecast")
            archived_deck = await create_deck(session, user, "Archived")
            cards = [await create_card(session, user, deck, f"card-{index}") for index in range(5)]
            archived_card = await create_card(session, user, archived_deck, "archived")
            today = user_today(timezone_name)
            cards[0].state = "review"
            cards[0].due_at = local_datetime(today - timedelta(days=1), timezone_name)
            cards[1].state = "review"
            cards[1].due_at = local_datetime(today, timezone_name)
            cards[2].state = "learning"
            cards[2].due_at = local_datetime(today + timedelta(days=2), timezone_name)
            cards[3].state = "new"
            cards[3].due_at = local_datetime(today + timedelta(days=1), timezone_name)
            cards[4].state = "review"
            cards[4].suspended = True
            cards[4].due_at = local_datetime(today + timedelta(days=1), timezone_name)
            archived_card.state = "review"
            archived_card.due_at = local_datetime(today + timedelta(days=1), timezone_name)
            await archive_deck(session, archived_deck)
            await session.commit()
            return today.isoformat(), (today + timedelta(days=2)).isoformat()

    today, future_day = asyncio.run(create_data())
    app = build_app(session_factory, monkeypatch)
    response = request(app, "/api/stats/forecast?days=3")

    assert response.status_code == 200
    assert response.json() == {
        "overdue": 1,
        "days": [{"date": today, "count": 1}, {"date": future_day, "count": 1}],
    }
    assert request(app, "/api/stats/forecast?days=0").status_code == 422
    assert request(app, "/api/stats/forecast?days=91").status_code == 422
