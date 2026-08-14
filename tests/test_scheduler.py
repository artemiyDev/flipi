from datetime import UTC, datetime

from bot.models import Card, Deck
from datetime import timedelta

from bot.services.scheduler import new_fsrs_card_json, review_with_fsrs, scheduler_kwargs_from_deck


def test_review_with_fsrs_updates_card_and_returns_log() -> None:
    card = Card(
        id=1,
        user_id=1,
        deck_id=1,
        note_id=1,
        due_at=datetime.now(UTC),
        state="new",
        fsrs_data=new_fsrs_card_json(),
        reps=0,
        lapses=0,
    )
    deck = Deck(id=1, user_id=1, name="Deck", desired_retention=0.9)

    log = review_with_fsrs(card, deck, 3)

    assert card.reps == 1
    assert card.state in {"learning", "review"}
    assert card.fsrs_data
    assert log.rating == 3
    assert log.next_due_at == card.due_at


def test_scheduler_kwargs_from_deck_uses_deck_options() -> None:
    deck = Deck(
        id=1,
        user_id=1,
        name="Deck",
        desired_retention=0.88,
        learning_steps_minutes=[2, 15],
        relearning_steps_minutes=[5],
        maximum_interval_days=180,
        enable_fuzzing=False,
    )

    kwargs = scheduler_kwargs_from_deck(deck)

    assert kwargs["desired_retention"] == 0.88
    assert kwargs["learning_steps"] == (timedelta(minutes=2), timedelta(minutes=15))
    assert kwargs["relearning_steps"] == (timedelta(minutes=5),)
    assert kwargs["maximum_interval"] == 180
    assert kwargs["enable_fuzzing"] is False
