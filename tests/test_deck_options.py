from bot.handlers.settings import _parse_setting_value
from bot.models import Deck
from bot.services.decks import DECK_OPTION_PRESETS
from bot.services.scheduler import scheduler_kwargs_from_deck


def test_parse_step_list_setting() -> None:
    assert _parse_setting_value("learning_steps_minutes", "1, 10,30") == [1, 10, 30]
    assert _parse_setting_value("learning_steps_minutes", "") is None


def test_all_deck_presets_build_scheduler_kwargs() -> None:
    for preset_name, preset in DECK_OPTION_PRESETS.items():
        deck = Deck(id=1, user_id=1, name=preset_name, **preset)

        kwargs = scheduler_kwargs_from_deck(deck)

        assert kwargs["learning_steps"]
        assert kwargs["relearning_steps"]
        assert kwargs["maximum_interval"] == preset["maximum_interval_days"]
