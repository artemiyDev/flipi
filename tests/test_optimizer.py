import asyncio
from datetime import UTC, datetime, timedelta

import pytest
from fsrs import ReviewLog as FsrsReviewLog

from bot.models import Card, Deck, ReviewLog, User
from bot.services import optimizer
from bot.services.optimizer import (
    InsufficientHistoryError,
    OptimizerUnavailableError,
    collect_deck_review_logs,
    optimize_deck,
)
from bot.services.scheduler import new_fsrs_card_json, review_with_fsrs, scheduler_kwargs_from_deck


async def _create_context(session, review_count: int) -> tuple[User, Deck]:
    user = User(telegram_id=10000 + review_count)
    session.add(user)
    await session.flush()
    deck = Deck(user_id=user.id, name="Optimizer")
    session.add(deck)
    await session.flush()
    card = Card(
        user_id=user.id,
        deck_id=deck.id,
        note_id=1,
        due_at=datetime.now(UTC),
        state="new",
        fsrs_data=new_fsrs_card_json(),
    )
    session.add(card)
    await session.flush()
    first_log = review_with_fsrs(card, deck, 3)
    session.add(first_log)
    for _ in range(review_count - 1):
        session.add(
            ReviewLog(
                user_id=user.id,
                deck_id=deck.id,
                card_id=card.id,
                rating=3,
                reviewed_at=first_log.reviewed_at,
                elapsed_ms=None,
                previous_due_at=first_log.previous_due_at,
                next_due_at=first_log.next_due_at,
                fsrs_review_log=first_log.fsrs_review_log,
            )
        )
    await session.commit()
    return user, deck


def test_collect_deck_review_logs_restores_and_sorts_logs(session_factory) -> None:
    async def check() -> None:
        async with session_factory() as session:
            user, deck = await _create_context(session, 1)
            card = await session.get(Card, 1)
            assert card is not None
            earlier = review_with_fsrs(card, deck, 1)
            earlier.reviewed_at -= timedelta(days=1)
            session.add(earlier)
            await session.commit()

            logs = await collect_deck_review_logs(session, deck)

            assert [log.rating.value for log in logs] == [1, 3]
            assert all(isinstance(log, FsrsReviewLog) for log in logs)
            assert user.id == deck.user_id

    asyncio.run(check())


def test_optimize_deck_handles_history_availability_and_success(session_factory, monkeypatch) -> None:
    async def check() -> None:
        async with session_factory() as session:
            user, deck = await _create_context(session, 1)
            with pytest.raises(InsufficientHistoryError, match="400") as error:
                await optimize_deck(session, user, deck)
            assert error.value.review_count == 1

        async with session_factory() as session:
            user, deck = await _create_context(session, 400)

            def unavailable(_review_logs) -> list[float]:
                raise ImportError

            monkeypatch.setattr(optimizer, "_compute_optimal_parameters", unavailable)
            with pytest.raises(OptimizerUnavailableError):
                await optimize_deck(session, user, deck)

            parameters = [float(index) for index in range(21)]
            monkeypatch.setattr(optimizer, "_compute_optimal_parameters", lambda _logs: parameters)
            result = await optimize_deck(session, user, deck)

            assert result["review_count"] == 400
            assert result["optimized_at"] == deck.fsrs_optimized_at
            assert deck.fsrs_parameters == parameters
            assert scheduler_kwargs_from_deck(deck)["parameters"] == tuple(parameters)

    asyncio.run(check())
