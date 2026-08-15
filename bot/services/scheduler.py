import json
from datetime import UTC, datetime, timedelta
from typing import Any

from fsrs import Card as FsrsCard
from fsrs import Rating, Scheduler

from bot.models import Card, Deck, ReviewLog


def _json_value(value: Any) -> dict[str, Any]:
    if value is None:
        return {}
    if isinstance(value, str):
        return json.loads(value)
    return value


def _json_text(value: Any) -> str:
    if isinstance(value, str):
        return value
    return json.dumps(value)


def _state_name(value: Any) -> str:
    return getattr(value, "name", str(value)).lower()


def new_fsrs_card_json() -> dict[str, Any]:
    return _json_value(FsrsCard().to_json())


def scheduler_kwargs_from_deck(deck: Deck) -> dict[str, Any]:
    scheduler_kwargs: dict[str, Any] = {
        "desired_retention": deck.desired_retention,
        "learning_steps": tuple(
            timedelta(minutes=value) for value in (deck.learning_steps_minutes or [1, 10])
        ),
        "relearning_steps": tuple(
            timedelta(minutes=value) for value in (deck.relearning_steps_minutes or [10])
        ),
        "maximum_interval": deck.maximum_interval_days,
        "enable_fuzzing": deck.enable_fuzzing,
    }
    if deck.fsrs_parameters:
        scheduler_kwargs["parameters"] = tuple(deck.fsrs_parameters)
    return scheduler_kwargs


def review_with_fsrs(
    card: Card,
    deck: Deck,
    rating_value: int,
    elapsed_ms: int | None = None,
) -> ReviewLog:
    scheduler = Scheduler(**scheduler_kwargs_from_deck(deck))
    fsrs_card = FsrsCard.from_json(_json_text(card.fsrs_data or new_fsrs_card_json()))
    previous_due = card.due_at

    reviewed_card, fsrs_log = scheduler.review_card(
        fsrs_card, Rating(rating_value), review_duration=elapsed_ms
    )
    due_at = reviewed_card.due
    if due_at.tzinfo is None:
        due_at = due_at.replace(tzinfo=UTC)

    card.fsrs_data = _json_value(reviewed_card.to_json())
    card.due_at = due_at
    card.state = _state_name(reviewed_card.state)
    card.reps += 1
    if rating_value == Rating.Again.value:
        card.lapses += 1

    return ReviewLog(
        user_id=card.user_id,
        deck_id=card.deck_id,
        card_id=card.id,
        rating=rating_value,
        reviewed_at=datetime.now(UTC),
        elapsed_ms=elapsed_ms,
        previous_due_at=previous_due,
        next_due_at=due_at,
        fsrs_review_log=_json_value(fsrs_log.to_json()),
    )


def preview_intervals(card: Card, deck: Deck) -> dict[str, str]:
    scheduler = Scheduler(**scheduler_kwargs_from_deck(deck))
    fsrs_card = FsrsCard.from_json(_json_text(card.fsrs_data or new_fsrs_card_json()))
    reviewed_at = datetime.now(UTC)
    intervals: dict[str, str] = {}
    for rating, name in ((1, "again"), (2, "hard"), (3, "good"), (4, "easy")):
        reviewed_card, _ = scheduler.review_card(
            fsrs_card, Rating(rating), review_datetime=reviewed_at
        )
        intervals[name] = format_interval(reviewed_card.due - reviewed_at)
    return intervals


def format_interval(interval: timedelta) -> str:
    minutes = max(1, round(interval.total_seconds() / 60))
    if minutes < 60:
        return f"{minutes} мин"
    hours = round(minutes / 60)
    if hours < 24:
        return f"{hours} ч"
    days = round(hours / 24)
    if days < 30:
        return f"{days} дн"
    return f"{max(1, round(days / 30))} мес"
