import asyncio
from datetime import UTC, date, datetime, timedelta
from types import SimpleNamespace

import httpx
import pytest
from sqlalchemy import func, select, update

from app.deps import get_current_user, get_db_session
from app.main import create_app
from bot.models import Card, DailyStudyCounter, Event, ReviewLog
from bot.services.cards import (
    create_basic_note,
    create_cloze_note,
    reset_card,
    set_card_due_in_days,
    set_card_suspended,
)
from bot.services.decks import archive_deck, create_deck
from bot.services.leeches import (
    LeechResumeConflictError,
    register_review_lapse,
    resume_leech,
)
from bot.services.study import SuspendedCardAnswerError, answer_card
from bot.services.users import get_or_create_user

TEST_TELEGRAM_ID = 7357001


def _build_app(session_factory, user_id: int):
    async def override_db_session():
        async with session_factory() as session:
            yield session

    async def override_current_user():
        return SimpleNamespace(id=user_id, timezone="UTC")

    app = create_app()
    app.dependency_overrides[get_db_session] = override_db_session
    app.dependency_overrides[get_current_user] = override_current_user
    return app


def _request(app, method: str, path: str, payload: dict | None = None) -> httpx.Response:
    async def send() -> httpx.Response:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            return await client.request(method, path, json=payload)

    return asyncio.run(send())


async def _create_user_card(
    session_factory,
    *,
    telegram_id: int = TEST_TELEGRAM_ID,
    deck_name: str = "Leeches",
    review_lapses: int = 0,
    state: str = "new",
) -> tuple[int, int]:
    async with session_factory() as session:
        user = await get_or_create_user(
            session,
            SimpleNamespace(
                id=telegram_id,
                username="leeches",
                full_name="Leech Tester",
                language_code="en",
            ),
        )
        deck = await create_deck(session, user, deck_name)
        note = await create_basic_note(session, user, deck, "question", "answer")
        card = (await session.scalars(select(Card).where(Card.note_id == note.id))).one()
        card.state = state
        card.review_lapses = review_lapses
        await session.commit()
        return user.id, card.id


def _policy_card(review_lapses: int) -> Card:
    return Card(
        id=1,
        user_id=1,
        deck_id=1,
        note_id=1,
        due_at=datetime.now(UTC),
        state="review",
        fsrs_data={},
        suspended=False,
        reps=0,
        lapses=0,
        review_lapses=review_lapses,
    )


def test_review_lapse_policy_counts_only_again_from_review() -> None:
    for previous_state in ("new", "learning", "relearning"):
        card = _policy_card(3)
        review = ReviewLog()
        assert register_review_lapse(card, review, previous_state, 1) is None
        assert card.review_lapses == 3
        assert card.suspended is False

    for rating in (2, 3, 4):
        card = _policy_card(3)
        review = ReviewLog()
        assert register_review_lapse(card, review, "review", rating) is None
        assert card.review_lapses == 3


@pytest.mark.parametrize(
    ("before", "alert"),
    [(2, None), (3, 4), (4, None), (5, 6), (6, None), (7, 8), (8, None), (9, 10)],
)
def test_review_lapse_policy_alerts_on_threshold_and_repeat_interval(
    before: int,
    alert: int | None,
) -> None:
    card = _policy_card(before)
    card.buried_until = date.today() + timedelta(days=1)
    review = ReviewLog()

    assert register_review_lapse(card, review, "review", 1) == alert
    assert card.review_lapses == before + 1
    assert review.leech_alert_lapses == alert
    assert card.suspended is (alert is not None)
    assert card.leech_suspended_lapses == alert
    if alert is not None:
        assert card.buried_until is None
    else:
        assert card.buried_until is not None


def test_api_leech_alert_is_atomic_replayable_and_excluded_from_goals(
    session_factory,
) -> None:
    user_id, card_id = asyncio.run(
        _create_user_card(session_factory, review_lapses=3, state="review")
    )
    app = _build_app(session_factory, user_id)
    payload = {
        "card_id": card_id,
        "rating": 1,
        "elapsed_ms": 240,
        "request_id": "leech-threshold-answer",
    }

    first = _request(app, "POST", "/api/study/answer", payload)
    rejected = _request(
        app,
        "POST",
        "/api/study/answer",
        {**payload, "request_id": "new-key-while-suspended"},
    )
    next_card = _request(app, "GET", "/api/study/next?deck_id=all")

    assert first.status_code == 200
    assert first.json()["leech"] == {"review_lapses": 4, "auto_suspended": True}
    assert first.json()["replayed"] is False
    assert rejected.status_code == 409
    assert next_card.status_code == 200
    assert next_card.json()["card_id"] is None
    assert next_card.json()["goals"]["full"] == {"remaining": 0, "achieved": True}

    async def inspect_after_answer() -> tuple[Card, ReviewLog, int, int, int]:
        async with session_factory() as session:
            card = await session.get(Card, card_id)
            review = (await session.scalars(select(ReviewLog))).one()
            counter = (await session.scalars(select(DailyStudyCounter))).one()
            review_events = await session.scalar(
                select(func.count(Event.id)).where(Event.name == "review_answer")
            )
            leech_events = await session.scalar(
                select(func.count(Event.id)).where(Event.name == "leech_detected")
            )
            return card, review, counter.reviews_done, int(review_events), int(leech_events)

    card, review, reviews_done, review_events, leech_events = asyncio.run(
        inspect_after_answer()
    )
    assert card.review_lapses == 4
    assert card.suspended is True
    assert card.leech_suspended_lapses == 4
    assert review.leech_alert_lapses == 4
    assert reviews_done == 1
    assert review_events == 1
    assert leech_events == 1

    resumed = _request(
        app,
        "POST",
        f"/api/cards/{card_id}/leech/resume",
        {"expected_review_lapses": 4},
    )
    resumed_again = _request(
        app,
        "POST",
        f"/api/cards/{card_id}/leech/resume",
        {"expected_review_lapses": 4},
    )
    replay_after_resume = _request(app, "POST", "/api/study/answer", payload)
    reset = _request(app, "POST", f"/api/cards/{card_id}/reset", {})
    replay_after_reset = _request(app, "POST", "/api/study/answer", payload)

    assert resumed.json() == {"ok": True, "review_lapses": 4, "replayed": False}
    assert resumed_again.json() == {"ok": True, "review_lapses": 4, "replayed": True}
    assert replay_after_resume.json()["leech"] == first.json()["leech"]
    assert replay_after_resume.json()["replayed"] is True
    assert reset.status_code == 200
    assert replay_after_reset.json()["leech"] == first.json()["leech"]
    assert replay_after_reset.json()["replayed"] is True

    async def inspect_after_replays() -> tuple[int, int, int, int]:
        async with session_factory() as session:
            card = await session.get(Card, card_id)
            reviews = await session.scalar(select(func.count(ReviewLog.id)))
            review_events = await session.scalar(
                select(func.count(Event.id)).where(Event.name == "review_answer")
            )
            leech_events = await session.scalar(
                select(func.count(Event.id)).where(Event.name == "leech_detected")
            )
            return card.review_lapses, int(reviews), int(review_events), int(leech_events)

    assert asyncio.run(inspect_after_replays()) == (0, 1, 1, 1)


def test_legacy_answer_reloads_locked_card_and_rejects_suspended_state(
    session_factory,
) -> None:
    user_id, card_id = asyncio.run(_create_user_card(session_factory))

    async def check_reload() -> None:
        async with session_factory() as session:
            card = await session.get(Card, card_id)
            assert card.review_lapses == 0
            await session.execute(
                update(Card)
                .where(Card.id == card_id)
                .values(state="review", review_lapses=5)
                .execution_options(synchronize_session=False)
            )
            await session.commit()
            assert card.review_lapses == 0

            user = SimpleNamespace(id=user_id, timezone="UTC")
            answered = await answer_card(session, user, card, 1)
            assert answered.review_lapses == 6
            assert answered.suspended is True
            assert answered.leech_suspended_lapses == 6

            await session.execute(
                update(Card)
                .where(Card.id == card_id)
                .values(suspended=True, leech_suspended_lapses=None)
                .execution_options(synchronize_session=False)
            )
            await session.commit()
            with pytest.raises(SuspendedCardAnswerError):
                await answer_card(session, user, card, 1)

        async with session_factory() as session:
            card = await session.get(Card, card_id)
            reviews = await session.scalar(select(func.count(ReviewLog.id)))
            assert card.review_lapses == 6
            assert card.reps == 1
            assert reviews == 1

    asyncio.run(check_reload())


def test_guarded_resume_rejects_non_alert_stale_and_manual_suspension(
    session_factory,
) -> None:
    user_id, card_id = asyncio.run(
        _create_user_card(session_factory, review_lapses=5, state="review")
    )

    async def check() -> None:
        async with session_factory() as session:
            user = SimpleNamespace(id=user_id)
            with pytest.raises(LeechResumeConflictError):
                await resume_leech(session, user, card_id, 5)

            card = await session.get(Card, card_id)
            card.review_lapses = 4
            card.suspended = True
            card.leech_suspended_lapses = 4
            await session.commit()

        async with session_factory() as session:
            user = SimpleNamespace(id=user_id)
            result = await resume_leech(session, user, card_id, 4)
            assert result is not None and result.replayed is False
            replay = await resume_leech(session, user, card_id, 4)
            assert replay is not None and replay.replayed is True

        async with session_factory() as session:
            card = await session.get(Card, card_id)
            card.suspended = True
            card.leech_suspended_lapses = None
            await session.commit()

        async with session_factory() as session:
            user = SimpleNamespace(id=user_id)
            with pytest.raises(LeechResumeConflictError):
                await resume_leech(session, user, card_id, 4)
            card = await session.get(Card, card_id)
            assert card.suspended is True
            assert card.leech_suspended_lapses is None

        async with session_factory() as session:
            card = await session.get(Card, card_id)
            card.review_lapses = 6
            card.leech_suspended_lapses = 6
            await session.commit()

        async with session_factory() as session:
            user = SimpleNamespace(id=user_id)
            with pytest.raises(LeechResumeConflictError):
                await resume_leech(session, user, card_id, 4)

    asyncio.run(check())


def test_manual_leech_mutations_reload_stale_card_before_clearing_marker(
    session_factory,
) -> None:
    user_id, card_id = asyncio.run(
        _create_user_card(session_factory, review_lapses=3, state="review")
    )

    async def check_manual_suspend() -> None:
        async with session_factory() as session:
            stale_card = await session.get(Card, card_id)
            await session.execute(
                update(Card)
                .where(Card.id == card_id)
                .values(
                    state="relearning",
                    review_lapses=4,
                    suspended=True,
                    leech_suspended_lapses=4,
                )
                .execution_options(synchronize_session=False)
            )
            await session.commit()
            assert stale_card.review_lapses == 3
            await set_card_suspended(session, stale_card, True)
            assert stale_card.review_lapses == 4
            assert stale_card.suspended is True
            assert stale_card.leech_suspended_lapses is None

        async with session_factory() as session:
            with pytest.raises(LeechResumeConflictError):
                await resume_leech(
                    session,
                    SimpleNamespace(id=user_id),
                    card_id,
                    4,
                )

    asyncio.run(check_manual_suspend())

    async def check_due_change() -> None:
        async with session_factory() as session:
            stale_card = await session.get(Card, card_id)
            await session.execute(
                update(Card)
                .where(Card.id == card_id)
                .values(
                    review_lapses=6,
                    suspended=True,
                    leech_suspended_lapses=6,
                )
                .execution_options(synchronize_session=False)
            )
            await session.commit()
            await set_card_due_in_days(session, stale_card, 2)
            assert stale_card.review_lapses == 6
            assert stale_card.suspended is False
            assert stale_card.leech_suspended_lapses is None

    asyncio.run(check_due_change())

    async def check_reset() -> None:
        async with session_factory() as session:
            stale_card = await session.get(Card, card_id)
            await session.execute(
                update(Card)
                .where(Card.id == card_id)
                .values(
                    state="relearning",
                    review_lapses=8,
                    suspended=True,
                    leech_suspended_lapses=8,
                )
                .execution_options(synchronize_session=False)
            )
            await session.commit()
            await reset_card(session, stale_card)
            assert stale_card.state == "new"
            assert stale_card.review_lapses == 0
            assert stale_card.suspended is False
            assert stale_card.leech_suspended_lapses is None

    asyncio.run(check_reset())


def test_guarded_later_is_idempotent_and_blocks_old_resume(session_factory) -> None:
    user_id, card_id = asyncio.run(
        _create_user_card(session_factory, review_lapses=4, state="relearning")
    )

    async def prepare() -> None:
        async with session_factory() as session:
            card = await session.get(Card, card_id)
            card.suspended = True
            card.leech_suspended_lapses = 4
            await session.commit()

    asyncio.run(prepare())
    app = _build_app(session_factory, user_id)
    path = f"/api/cards/{card_id}/leech/later"

    first = _request(app, "POST", path, {"expected_review_lapses": 4})
    replay = _request(app, "POST", path, {"expected_review_lapses": 4})
    stale_resume = _request(
        app,
        "POST",
        f"/api/cards/{card_id}/leech/resume",
        {"expected_review_lapses": 4},
    )
    non_alert = _request(app, "POST", path, {"expected_review_lapses": 5})

    assert first.json() == {"ok": True, "review_lapses": 4, "replayed": False}
    assert replay.json() == {"ok": True, "review_lapses": 4, "replayed": True}
    assert stale_resume.status_code == 409
    assert non_alert.status_code == 409

    async def inspect() -> tuple[bool, int | None, int]:
        async with session_factory() as session:
            card = await session.get(Card, card_id)
            event_count = await session.scalar(
                select(func.count(Event.id)).where(Event.name == "leech_deferred")
            )
            return card.suspended, card.leech_suspended_lapses, int(event_count)

    assert asyncio.run(inspect()) == (True, None, 1)


def test_leech_is_per_card_and_manual_actions_preserve_review_lapses(
    session_factory,
) -> None:
    async def create_data() -> tuple[int, int, int, int]:
        async with session_factory() as session:
            user = await get_or_create_user(
                session,
                SimpleNamespace(
                    id=TEST_TELEGRAM_ID,
                    username="siblings",
                    full_name="Siblings",
                    language_code="en",
                ),
            )
            deck = await create_deck(session, user, "Siblings")
            note = await create_basic_note(
                session,
                user,
                deck,
                "front",
                "back",
                create_reverse=True,
            )
            cards = list(
                (
                    await session.scalars(
                        select(Card).where(Card.note_id == note.id).order_by(Card.id)
                    )
                ).all()
            )
            cards[0].state = "review"
            cards[0].review_lapses = 3
            await session.commit()
            return user.id, note.id, cards[0].id, cards[1].id

    user_id, note_id, card_id, sibling_id = asyncio.run(create_data())
    app = _build_app(session_factory, user_id)
    answered = _request(
        app,
        "POST",
        "/api/study/answer",
        {
            "card_id": card_id,
            "rating": 1,
            "request_id": "per-card-leech",
        },
    )
    edited = _request(
        app,
        "PATCH",
        f"/api/notes/{note_id}",
        {"front": "clearer front"},
    )
    due_changed = _request(
        app,
        "POST",
        f"/api/cards/{card_id}/due",
        {"date": datetime.now(UTC).date().isoformat()},
    )
    manually_suspended = _request(
        app,
        "POST",
        f"/api/cards/{card_id}/suspend",
        {"value": True},
    )
    manually_resumed = _request(
        app,
        "POST",
        f"/api/cards/{card_id}/suspend",
        {"value": False},
    )

    assert answered.status_code == 200
    assert edited.status_code == 200
    assert due_changed.status_code == 200
    assert manually_suspended.status_code == 200
    assert manually_resumed.status_code == 200

    async def inspect_before_reset() -> tuple[Card, Card]:
        async with session_factory() as session:
            return await session.get(Card, card_id), await session.get(Card, sibling_id)

    card, sibling = asyncio.run(inspect_before_reset())
    assert card.review_lapses == 4
    assert card.suspended is False
    assert card.leech_suspended_lapses is None
    assert sibling.review_lapses == 0
    assert sibling.suspended is False

    reset = _request(app, "POST", f"/api/cards/{card_id}/reset", {})
    assert reset.status_code == 200

    async def inspect_reset() -> tuple[int, int | None]:
        async with session_factory() as session:
            card = await session.get(Card, card_id)
            return card.review_lapses, card.leech_suspended_lapses

    assert asyncio.run(inspect_reset()) == (0, None)


def test_cloze_sibling_has_independent_leech_counter(session_factory) -> None:
    async def create_data() -> tuple[int, int, int]:
        async with session_factory() as session:
            user = await get_or_create_user(
                session,
                SimpleNamespace(
                    id=TEST_TELEGRAM_ID,
                    username="cloze-siblings",
                    full_name="Cloze Siblings",
                    language_code="en",
                ),
            )
            deck = await create_deck(session, user, "Cloze siblings")
            note = await create_cloze_note(
                session,
                user,
                deck,
                "{{c1::Paris}} is in {{c2::France}}.",
            )
            cards = list(
                (
                    await session.scalars(
                        select(Card).where(Card.note_id == note.id).order_by(Card.id)
                    )
                ).all()
            )
            cards[0].state = "review"
            cards[0].review_lapses = 3
            await session.commit()
            return user.id, cards[0].id, cards[1].id

    user_id, first_card_id, sibling_card_id = asyncio.run(create_data())
    app = _build_app(session_factory, user_id)
    response = _request(
        app,
        "POST",
        "/api/study/answer",
        {
            "card_id": first_card_id,
            "rating": 1,
            "request_id": "cloze-per-card-leech",
        },
    )

    assert response.status_code == 200
    assert response.json()["leech"] == {"review_lapses": 4, "auto_suspended": True}

    async def inspect() -> tuple[Card, Card]:
        async with session_factory() as session:
            return (
                await session.get(Card, first_card_id),
                await session.get(Card, sibling_card_id),
            )

    first_card, sibling_card = asyncio.run(inspect())
    assert first_card.review_lapses == 4
    assert first_card.suspended is True
    assert sibling_card.review_lapses == 0
    assert sibling_card.suspended is False


def test_search_and_card_api_expose_user_scoped_leeches(session_factory) -> None:
    async def create_data() -> tuple[int, int]:
        async with session_factory() as session:
            user = await get_or_create_user(
                session,
                SimpleNamespace(
                    id=TEST_TELEGRAM_ID,
                    username="owner",
                    full_name="Owner",
                    language_code="en",
                ),
            )
            active = await create_deck(session, user, "Active")
            first = await create_basic_note(session, user, active, "leech", "answer")
            second = await create_basic_note(session, user, active, "normal", "answer")
            first_card = (await session.scalars(select(Card).where(Card.note_id == first.id))).one()
            second_card = (await session.scalars(select(Card).where(Card.note_id == second.id))).one()
            first_card.review_lapses = 4
            second_card.review_lapses = 3

            archived = await create_deck(session, user, "Archived")
            archived_note = await create_basic_note(session, user, archived, "old", "answer")
            archived_card = (
                await session.scalars(select(Card).where(Card.note_id == archived_note.id))
            ).one()
            archived_card.review_lapses = 8
            await archive_deck(session, archived)

            foreign = await get_or_create_user(
                session,
                SimpleNamespace(
                    id=TEST_TELEGRAM_ID + 1,
                    username="foreign",
                    full_name="Foreign",
                    language_code="en",
                ),
            )
            foreign_deck = await create_deck(session, foreign, "Foreign")
            foreign_note = await create_basic_note(
                session, foreign, foreign_deck, "foreign", "answer"
            )
            foreign_card = (
                await session.scalars(select(Card).where(Card.note_id == foreign_note.id))
            ).one()
            foreign_card.review_lapses = 6
            await session.commit()
            return user.id, first_card.id

    user_id, card_id = asyncio.run(create_data())
    app = _build_app(session_factory, user_id)

    search = _request(app, "GET", "/api/cards/search?q=is:leech")
    details = _request(app, "GET", f"/api/cards/{card_id}")

    assert search.status_code == 200
    assert search.json()["total"] == 1
    assert search.json()["items"][0]["card_id"] == card_id
    assert search.json()["items"][0]["is_leech"] is True
    assert search.json()["items"][0]["review_lapses"] == 4
    assert details.status_code == 200
    assert details.json()["is_leech"] is True
    assert details.json()["review_lapses"] == 4
