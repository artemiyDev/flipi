import asyncio
import hashlib
import hmac
import json
import time
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from urllib.parse import urlencode

import httpx
import pytest
from sqlalchemy import select

from app.deps import get_db_session
from app.main import create_app
from bot.models import Card, DailyStudyCounter, MediaFile, NoteStyle, ReviewLog, User
from bot.services.decks import archive_deck, create_deck
from bot.services.users import get_or_create_user

TEST_BOT_TOKEN = "test-bot-token"
TEST_TELEGRAM_ID = 123456789


class TelegramUser:
    id = TEST_TELEGRAM_ID
    username = "testuser"
    full_name = "Test User"
    language_code = "en"


def signed_init_data(auth_date: int | None = None, telegram_id: int = TEST_TELEGRAM_ID) -> str:
    data = {
        "auth_date": str(auth_date if auth_date is not None else int(time.time())),
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


def post_request(
    app,
    path: str,
    payload: dict,
    headers: dict[str, str] | None = None,
) -> httpx.Response:
    async def send() -> httpx.Response:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            return await client.post(path, json=payload, headers=headers)

    return asyncio.run(send())


def patch_request(
    app,
    path: str,
    payload: dict,
    headers: dict[str, str] | None = None,
) -> httpx.Response:
    async def send() -> httpx.Response:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            return await client.patch(path, json=payload, headers=headers)

    return asyncio.run(send())


def delete_request(
    app,
    path: str,
    headers: dict[str, str] | None = None,
) -> httpx.Response:
    async def send() -> httpx.Response:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            return await client.delete(path, headers=headers)

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


def test_api_manages_deck_lifecycle(session_factory, monkeypatch) -> None:
    app = build_app(session_factory, monkeypatch)
    headers = {"X-Telegram-Init-Data": signed_init_data()}

    created = post_request(
        app,
        "/api/decks",
        {"name": "Spanish", "description": "Practice vocabulary"},
        headers,
    )

    assert created.status_code == 201
    deck = created.json()
    assert deck["name"] == "Spanish"
    assert deck["description"] == "Practice vocabulary"
    assert deck["is_archived"] is False
    assert deck["counts"] == {"new": 0, "learning": 0, "review": 0}

    details = request(app, f"/api/decks/{deck['id']}", headers)
    renamed = patch_request(app, f"/api/decks/{deck['id']}", {"name": "Español"}, headers)
    archived = post_request(app, f"/api/decks/{deck['id']}/archive", {}, headers)
    active_decks = request(app, "/api/decks", headers)
    archived_decks = request(app, "/api/decks/archived", headers)
    restored = post_request(app, f"/api/decks/{deck['id']}/restore", {}, headers)

    assert details.status_code == 200
    assert renamed.status_code == 200
    assert renamed.json()["name"] == "Español"
    assert archived.status_code == 200
    assert archived.json()["is_archived"] is True
    assert active_decks.json() == []
    assert archived_decks.json() == [{"id": deck["id"], "name": "Español"}]
    assert restored.status_code == 200
    assert restored.json()["is_archived"] is False
    assert request(app, "/api/decks", headers).json()[0]["id"] == deck["id"]


def test_api_rejects_invalid_or_duplicate_root_deck_names(session_factory, monkeypatch) -> None:
    app = build_app(session_factory, monkeypatch)
    headers = {"X-Telegram-Init-Data": signed_init_data()}

    invalid_hierarchy = post_request(app, "/api/decks", {"name": "Languages::Spanish"}, headers)
    invalid_empty = post_request(app, "/api/decks", {"name": "  "}, headers)
    created = post_request(app, "/api/decks", {"name": "Spanish"}, headers)
    duplicate = post_request(app, "/api/decks", {"name": "Spanish"}, headers)
    invalid_rename = patch_request(
        app, f"/api/decks/{created.json()['id']}", {"name": "Spanish::Verbs"}, headers
    )

    assert invalid_hierarchy.status_code == 422
    assert invalid_empty.status_code == 422
    assert created.status_code == 201
    assert duplicate.status_code == 409
    assert invalid_rename.status_code == 422


def test_api_updates_deck_settings_and_applies_presets(session_factory, monkeypatch) -> None:
    app = build_app(session_factory, monkeypatch)
    headers = {"X-Telegram-Init-Data": signed_init_data()}
    deck_id = post_request(app, "/api/decks", {"name": "Spanish"}, headers).json()["id"]

    updated = patch_request(
        app,
        f"/api/decks/{deck_id}/settings",
        {"new_cards_per_day": 123},
        headers,
    )
    invalid_number = patch_request(
        app,
        f"/api/decks/{deck_id}/settings",
        {"new_cards_per_day": 5001},
        headers,
    )
    invalid_steps = patch_request(
        app,
        f"/api/decks/{deck_id}/settings",
        {"learning_steps_minutes": [0]},
        headers,
    )
    presets = request(app, "/api/decks/presets", headers)
    applied = post_request(app, f"/api/decks/{deck_id}/preset", {"name": "intense"}, headers)

    assert updated.status_code == 200
    assert updated.json()["settings"]["new_cards_per_day"] == 123
    assert updated.json()["settings"]["option_preset"] == "custom"
    assert invalid_number.status_code == 422
    assert invalid_steps.status_code == 422
    assert presets.status_code == 200
    assert set(presets.json()) == {"light", "balanced", "intense", "exam"}
    assert applied.status_code == 200
    assert applied.json()["settings"]["option_preset"] == "intense"
    assert applied.json()["settings"]["new_cards_per_day"] == presets.json()["intense"]["new_cards_per_day"]


@pytest.mark.parametrize(
    ("method", "path_suffix", "payload"),
    [
        ("get", "", {}),
        ("patch", "", {"name": "Other"}),
        ("post", "/archive", {}),
        ("post", "/restore", {}),
        ("patch", "/settings", {"new_cards_per_day": 10}),
        ("post", "/preset", {"name": "balanced"}),
    ],
)
def test_api_deck_routes_hide_foreign_decks(
    session_factory, monkeypatch, method: str, path_suffix: str, payload: dict
) -> None:
    app = build_app(session_factory, monkeypatch)
    owner_headers = {"X-Telegram-Init-Data": signed_init_data()}
    foreign_headers = {"X-Telegram-Init-Data": signed_init_data(telegram_id=987654)}
    deck_id = post_request(app, "/api/decks", {"name": "Spanish"}, owner_headers).json()["id"]
    path = f"/api/decks/{deck_id}{path_suffix}"

    if method == "get":
        response = request(app, path, foreign_headers)
    elif method == "patch":
        response = patch_request(app, path, payload, foreign_headers)
    else:
        response = post_request(app, path, payload, foreign_headers)

    assert response.status_code == 404


def test_healthz_does_not_require_authorization(session_factory, monkeypatch) -> None:
    response = request(build_app(session_factory, monkeypatch), "/api/healthz")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "database": True}


def test_study_next_renders_html_media_and_has_no_side_effects(session_factory, monkeypatch) -> None:
    from bot.services.cards import create_basic_note

    async def create_data() -> tuple[int, int]:
        async with session_factory() as session:
            user = await get_or_create_user(session, TelegramUser())
            deck = await create_deck(session, user, "Spanish")
            note = await create_basic_note(
                session,
                user,
                deck,
                '<b>hablar</b><ul><li>verb</li></ul><script>alert(1)</script>'
                '<img src="https://evil.example/x.png" onerror="alert(1)"> [media:word.png]',
                "to speak [sound:word.mp3]",
            )
            media = MediaFile(
                user_id=user.id,
                deck_id=deck.id,
                original_name="word.png",
                content_type="image/png",
                size_bytes=3,
                sha256="a" * 64,
                content=b"png",
            )
            sound = MediaFile(
                user_id=user.id,
                deck_id=deck.id,
                original_name="word.mp3",
                content_type="audio/mpeg",
                size_bytes=3,
                sha256="b" * 64,
                content=b"mp3",
            )
            session.add_all([media, sound])
            await session.commit()
            card = (await session.execute(Card.__table__.select())).first()[0]
            return card, media.id

    card_id, media_id = asyncio.run(create_data())
    app = build_app(session_factory, monkeypatch)
    headers = {"X-Telegram-Init-Data": signed_init_data()}

    first = request(app, "/api/study/next?deck_id=all", headers)
    second = request(app, "/api/study/next?deck_id=all", headers)

    assert first.status_code == 200
    assert second.status_code == 200
    payload = first.json()
    assert payload["card_id"] == card_id
    assert payload["progress"] == {"new": 1, "learning": 0, "review": 0}
    assert set(payload["intervals"]) == {"again", "hard", "good", "easy"}
    assert "<b>hablar</b>" in payload["question_html"]
    assert "<ul><li>verb</li></ul>" in payload["question_html"]
    assert "script" not in payload["question_html"]
    assert "evil.example" not in payload["question_html"]
    assert f'<img src="/api/media/{media_id}">' in payload["question_html"]
    assert {item["name"] for item in payload["media"]} == {"word.png", "word.mp3"}
    assert payload["card_css"] is None

    async def card_state() -> tuple[str, int]:
        async with session_factory() as session:
            card = await session.get(Card, card_id)
            return card.state, card.reps

    assert asyncio.run(card_state()) == ("new", 0)


def test_study_and_card_details_return_model_css(session_factory, monkeypatch) -> None:
    from bot.services.cards import create_basic_note

    async def create_data() -> int:
        async with session_factory() as session:
            user = await get_or_create_user(session, TelegramUser())
            deck = await create_deck(session, user, "Styled")
            note = await create_basic_note(
                session,
                user,
                deck,
                "question",
                "answer",
                anki_model_id="42",
            )
            session.add(
                NoteStyle(
                    user_id=user.id,
                    anki_model_id="42",
                    css='.card { background: url("https://evil.example/card.png"); color: red; }',
                )
            )
            await session.commit()
            return (await session.execute(select(Card.id).where(Card.note_id == note.id))).scalar_one()

    card_id = asyncio.run(create_data())
    app = build_app(session_factory, monkeypatch)
    headers = {"X-Telegram-Init-Data": signed_init_data()}

    next_card = request(app, "/api/study/next?deck_id=all", headers)
    details = request(app, f"/api/cards/{card_id}", headers)

    assert next_card.status_code == 200
    assert details.status_code == 200
    assert next_card.json()["card_css"] == ".card { background: ; color: red; }"
    assert details.json()["card_css"] == ".card { background: ; color: red; }"


def test_study_answer_records_review_buries_siblings_and_returns_media(session_factory, monkeypatch) -> None:
    from bot.services.cards import create_basic_note

    async def create_data() -> tuple[int, int, int]:
        async with session_factory() as session:
            user = await get_or_create_user(session, TelegramUser())
            deck = await create_deck(session, user, "Spanish")
            note = await create_basic_note(
                session, user, deck, "front", "back", create_reverse=True
            )
            cards = list((await session.execute(Card.__table__.select())).all())
            return deck.id, cards[0][0], cards[1][0]

    deck_id, card_id, sibling_id = asyncio.run(create_data())
    app = build_app(session_factory, monkeypatch)
    headers = {"X-Telegram-Init-Data": signed_init_data()}

    invalid = post_request(app, "/api/study/answer", {"card_id": card_id, "rating": 5}, headers)
    response = post_request(
        app,
        "/api/study/answer",
        {"card_id": card_id, "rating": 3, "elapsed_ms": 321},
        headers,
    )
    next_response = request(app, f"/api/study/next?deck_id={deck_id}", headers)

    assert invalid.status_code == 422
    assert response.status_code == 200
    assert response.json()["ok"] is True
    assert response.json()["state"] in {"learning", "review"}
    assert next_response.json()["card_id"] is None

    foreign_response = post_request(
        app,
        "/api/study/answer",
        {"card_id": card_id, "rating": 3},
        {"X-Telegram-Init-Data": signed_init_data(telegram_id=987654)},
    )
    assert foreign_response.status_code == 404

    async def review_state() -> tuple[int | None, bool]:
        async with session_factory() as session:
            review = (await session.execute(select(ReviewLog))).scalar_one()
            sibling = await session.get(Card, sibling_id)
            return review.elapsed_ms, sibling.buried_until is not None

    assert asyncio.run(review_state()) == (321, True)

    async def make_card_due() -> str:
        async with session_factory() as session:
            card = await session.get(Card, card_id)
            card.due_at = datetime.now(UTC)
            await session.commit()
            return card.state

    for _ in range(3):
        if asyncio.run(make_card_due()) == "review":
            break
        response = post_request(
            app, "/api/study/answer", {"card_id": card_id, "rating": 3}, headers
        )
        assert response.status_code == 200
    assert response.json()["state"] == "review"


def test_again_is_counted_once_and_keeps_today_learning_step_in_global_goal(
    session_factory,
    monkeypatch,
) -> None:
    import app.api as api_module
    import bot.services.cards as cards_service
    import bot.services.study as study_service
    import bot.services.timezones as timezones_service
    from bot.services.cards import create_basic_note

    fixed_now = datetime(2026, 3, 10, 12, tzinfo=UTC)

    class FixedDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            if tz is None:
                return fixed_now.replace(tzinfo=None)
            return fixed_now.astimezone(tz)

    def fixed_again_review(card, deck, rating_value: int, elapsed_ms: int | None = None):
        assert rating_value == 1
        previous_due = card.due_at
        card.state = "learning"
        card.due_at = fixed_now + timedelta(minutes=1)
        card.reps += 1
        card.lapses += 1
        return ReviewLog(
            user_id=card.user_id,
            deck_id=deck.id,
            card_id=card.id,
            rating=rating_value,
            reviewed_at=fixed_now,
            elapsed_ms=elapsed_ms,
            previous_due_at=previous_due,
            next_due_at=card.due_at,
        )

    monkeypatch.setattr(api_module, "datetime", FixedDateTime)
    monkeypatch.setattr(cards_service, "datetime", FixedDateTime)
    monkeypatch.setattr(timezones_service, "datetime", FixedDateTime)
    monkeypatch.setattr(study_service, "review_with_fsrs", fixed_again_review)

    async def create_data() -> tuple[int, int]:
        async with session_factory() as session:
            user = await get_or_create_user(session, TelegramUser())
            user.timezone = "UTC"
            deck = await create_deck(session, user, "Again")
            note = await create_basic_note(session, user, deck, "front", "back")
            card = (
                await session.execute(select(Card).where(Card.note_id == note.id))
            ).scalar_one()
            card.due_at = fixed_now
            await session.commit()
            return deck.id, card.id

    deck_id, card_id = asyncio.run(create_data())
    app = build_app(session_factory, monkeypatch)
    headers = {"X-Telegram-Init-Data": signed_init_data()}
    answer_payload = {
        "card_id": card_id,
        "rating": 1,
        "elapsed_ms": 100,
        "request_id": "again-goal-once",
    }

    first_answer = post_request(app, "/api/study/answer", answer_payload, headers)
    replayed_answer = post_request(app, "/api/study/answer", answer_payload, headers)
    next_response = request(app, f"/api/study/next?deck_id={deck_id}", headers)

    assert first_answer.status_code == 200
    assert first_answer.json()["replayed"] is False
    assert replayed_answer.status_code == 200
    assert replayed_answer.json()["replayed"] is True
    assert next_response.json() == {
        "card_id": None,
        "done_today": 1,
        "goals": {
            "streak": {"done": 0, "target": 10, "achieved": False},
            "full": {"remaining": 1, "achieved": False},
        },
    }

    async def persisted_state() -> tuple[int, int, str, int, int]:
        async with session_factory() as session:
            reviews = list((await session.execute(select(ReviewLog))).scalars())
            counter = (await session.execute(select(DailyStudyCounter))).scalar_one()
            card = await session.get(Card, card_id)
            return len(reviews), counter.new_seen, card.state, card.reps, card.lapses

    assert asyncio.run(persisted_state()) == (1, 1, "learning", 1, 1)


def test_media_endpoint_is_private_to_the_current_user(session_factory, monkeypatch) -> None:
    async def create_data() -> int:
        async with session_factory() as session:
            user = await get_or_create_user(session, TelegramUser())
            media = MediaFile(
                user_id=user.id,
                original_name="word.png",
                content_type="image/png",
                size_bytes=3,
                sha256="c" * 64,
                content=b"png",
            )
            session.add(media)
            await session.commit()
            return media.id

    media_id = asyncio.run(create_data())
    app = build_app(session_factory, monkeypatch)
    headers = {"X-Telegram-Init-Data": signed_init_data()}

    response = request(app, f"/api/media/{media_id}", headers)

    assert response.status_code == 200
    assert response.content == b"png"
    assert response.headers["content-type"] == "image/png"
    assert response.headers["cache-control"] == "private, max-age=86400"

    foreign_response = request(
        app,
        f"/api/media/{media_id}",
        {"X-Telegram-Init-Data": signed_init_data(telegram_id=987654)},
    )
    assert foreign_response.status_code == 404


def test_study_next_all_iterates_decks_then_reports_done_today(session_factory, monkeypatch) -> None:
    from bot.services.cards import create_basic_note

    async def create_data() -> None:
        async with session_factory() as session:
            user = await get_or_create_user(session, TelegramUser())
            alpha = await create_deck(session, user, "Alpha")
            beta = await create_deck(session, user, "Beta")
            await create_basic_note(session, user, alpha, "a", "a")
            await create_basic_note(session, user, beta, "b", "b")

    asyncio.run(create_data())
    app = build_app(session_factory, monkeypatch)
    headers = {"X-Telegram-Init-Data": signed_init_data()}

    first = request(app, "/api/study/next?deck_id=all", headers).json()
    post_request(app, "/api/study/answer", {"card_id": first["card_id"], "rating": 3}, headers)
    second = request(app, "/api/study/next?deck_id=all", headers).json()
    post_request(app, "/api/study/answer", {"card_id": second["card_id"], "rating": 3}, headers)
    done = request(app, "/api/study/next?deck_id=all", headers)

    assert first["deck_name"] == "Alpha"
    assert first["goals"] == {
        "streak": {"done": 0, "target": 10, "achieved": False},
        "full": {"remaining": 2, "achieved": False},
    }
    assert second["deck_name"] == "Beta"
    done_payload = done.json()
    assert done_payload["card_id"] is None
    assert done_payload["done_today"] == 2
    assert done_payload["goals"]["streak"] == {
        "done": 2,
        "target": 10,
        "achieved": False,
    }
    assert set(done_payload["goals"]) == {"streak", "full"}


def test_study_next_for_empty_deck_returns_global_goals(session_factory, monkeypatch) -> None:
    from bot.services.cards import create_basic_note

    async def create_data() -> int:
        async with session_factory() as session:
            user = await get_or_create_user(session, TelegramUser())
            empty = await create_deck(session, user, "Empty")
            other = await create_deck(session, user, "Other")
            await create_basic_note(session, user, other, "other", "other")
            return empty.id

    empty_deck_id = asyncio.run(create_data())
    app = build_app(session_factory, monkeypatch)
    headers = {"X-Telegram-Init-Data": signed_init_data()}

    response = request(app, f"/api/study/next?deck_id={empty_deck_id}", headers)

    assert response.status_code == 200
    assert response.json() == {
        "card_id": None,
        "done_today": 0,
        "goals": {
            "streak": {"done": 0, "target": 10, "achieved": False},
            "full": {"remaining": 1, "achieved": False},
        },
    }


def test_cards_api_creates_reverse_cards_and_renders_sanitized_details(
    session_factory, monkeypatch
) -> None:
    app = build_app(session_factory, monkeypatch)
    headers = {"X-Telegram-Init-Data": signed_init_data()}
    foreign_headers = {"X-Telegram-Init-Data": signed_init_data(telegram_id=987654)}
    deck_id = post_request(app, "/api/decks", {"name": "Russian"}, headers).json()["id"]
    foreign_deck_id = post_request(
        app, "/api/decks", {"name": "Foreign"}, foreign_headers
    ).json()["id"]

    created = post_request(
        app,
        "/api/cards",
        {
            "deck_id": deck_id,
            "front": "<b>слово</b><script>alert(1)</script>",
            "back": "word",
            "tags": ["language"],
            "reverse": True,
        },
        headers,
    )
    cards = request(app, "/api/cards/search?q=слово", headers).json()
    blank = post_request(
        app, "/api/cards", {"deck_id": deck_id, "front": "  ", "back": "word"}, headers
    )
    foreign = post_request(
        app,
        "/api/cards",
        {"deck_id": foreign_deck_id, "front": "word", "back": "слово"},
        headers,
    )
    details = request(app, f"/api/cards/{cards['items'][0]['card_id']}", headers)

    assert created.status_code == 201
    assert created.json()["note_id"]
    assert cards["total"] == 2
    assert {item["note_id"] for item in cards["items"]} == {created.json()["note_id"]}
    assert blank.status_code == 422
    assert foreign.status_code == 404
    assert details.status_code == 200
    assert "<b>слово</b>" in details.json()["question_html"]
    assert "script" not in details.json()["question_html"]
    assert details.json()["card_css"] is None


def test_cards_api_search_actions_and_note_editing(session_factory, monkeypatch) -> None:
    app = build_app(session_factory, monkeypatch)
    headers = {"X-Telegram-Init-Data": signed_init_data()}
    deck_id = post_request(app, "/api/decks", {"name": "Languages"}, headers).json()["id"]

    first = post_request(
        app,
        "/api/cards",
        {"deck_id": deck_id, "front": "unique-search-term", "back": "one", "tags": ["french"]},
        headers,
    )
    post_request(
        app,
        "/api/cards",
        {"deck_id": deck_id, "front": "second", "back": "two", "tags": ["spanish"]},
        headers,
    )
    all_cards = request(app, "/api/cards/search", headers).json()
    card_id = next(
        item["card_id"] for item in all_cards["items"] if item["note_id"] == first.json()["note_id"]
    )

    tag_search = request(app, "/api/cards/search?q=tag:french", headers).json()
    state_search = request(app, "/api/cards/search?q=state:new", headers).json()
    deck_search = request(app, "/api/cards/search?q=deck:Languages", headers).json()
    text_search = request(app, "/api/cards/search?q=unique-search-term", headers).json()
    page = request(app, "/api/cards/search?limit=1&offset=1", headers).json()
    suspended = post_request(app, f"/api/cards/{card_id}/suspend", {"value": True}, headers)
    suspended_search = request(app, "/api/cards/search?q=is:suspended", headers).json()
    unsuspended = post_request(app, f"/api/cards/{card_id}/suspend", {"value": False}, headers)
    buried = post_request(app, f"/api/cards/{card_id}/bury", {}, headers)
    buried_search = request(app, "/api/cards/search?q=is:buried", headers).json()
    flagged = post_request(app, f"/api/cards/{card_id}/flag", {"color": "red"}, headers)
    flag_search = request(app, "/api/cards/search?q=flag:red", headers).json()
    due = post_request(app, f"/api/cards/{card_id}/due", {"date": "2030-01-01"}, headers)
    after_due = request(app, f"/api/cards/{card_id}", headers).json()
    reset = post_request(app, f"/api/cards/{card_id}/reset", {}, headers)
    edited = patch_request(
        app,
        f"/api/notes/{first.json()['note_id']}",
        {"front": "updated front", "tags": ["updated"], "fields": {"Front": "updated front"}},
        headers,
    )
    details = request(app, f"/api/cards/{card_id}", headers).json()

    assert tag_search["total"] == 1
    assert state_search["total"] == 2
    assert deck_search["total"] == 2
    assert text_search["total"] == 1
    assert page["total"] == 2
    assert len(page["items"]) == 1
    assert suspended.status_code == 200
    assert suspended_search["total"] == 1
    assert unsuspended.status_code == 200
    assert buried.status_code == 200
    assert buried_search["total"] == 1
    assert flagged.status_code == 200
    assert flag_search["total"] == 1
    assert due.status_code == 200
    assert after_due["state"] == "review"
    assert after_due["due"].startswith("2030-01-01")
    assert reset.status_code == 200
    assert edited.status_code == 200
    assert details["front"] == "updated front"
    assert details["tags"] == ["updated"]
    assert details["fields"] == {"Front": "updated front"}
    assert details["state"] == "new"
    assert details["suspended"] is False
    assert details["buried_until"] is None


def test_cards_api_deletes_notes_and_hides_foreign_notes(session_factory, monkeypatch) -> None:
    app = build_app(session_factory, monkeypatch)
    headers = {"X-Telegram-Init-Data": signed_init_data()}
    foreign_headers = {"X-Telegram-Init-Data": signed_init_data(telegram_id=987654)}
    deck_id = post_request(app, "/api/decks", {"name": "Delete"}, headers).json()["id"]
    created = post_request(
        app,
        "/api/cards",
        {"deck_id": deck_id, "front": "delete me", "back": "gone", "reverse": True},
        headers,
    )
    cards = request(app, "/api/cards/search?q=delete", headers).json()
    foreign_delete = delete_request(
        app, f"/api/notes/{created.json()['note_id']}", foreign_headers
    )
    deleted = delete_request(app, f"/api/notes/{created.json()['note_id']}", headers)
    remaining = request(app, "/api/cards/search?q=delete", headers).json()
    missing = request(app, f"/api/cards/{cards['items'][0]['card_id']}", headers)

    assert cards["total"] == 2
    assert foreign_delete.status_code == 404
    assert deleted.status_code == 204
    assert remaining == {"total": 0, "items": []}
    assert missing.status_code == 404
