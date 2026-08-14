from bot.models import Card, Note
from bot.services.cards import card_answer, card_question


def test_card_question_renders_anki_fields() -> None:
    note = Note(
        id=1,
        user_id=1,
        deck_id=1,
        note_type="Basic",
        fields={"Word": "bonjour", "Meaning": "hello"},
        front="fallback front",
        back="fallback back",
    )
    card = Card(
        id=1,
        user_id=1,
        deck_id=1,
        note_id=1,
        note=note,
        direction="front_back",
        template_ord=0,
        question_template="{{Word}}",
        answer_template="{{FrontSide}}<br>{{Meaning}}",
    )

    assert card_question(card) == "bonjour"
    assert card_answer(card) == "bonjour\nhello"


def test_card_question_renders_cloze() -> None:
    note = Note(
        id=1,
        user_id=1,
        deck_id=1,
        note_type="Cloze",
        fields={"Text": "Capital is {{c1::Paris::city}}"},
        front="fallback front",
        back="fallback back",
    )
    card = Card(
        id=1,
        user_id=1,
        deck_id=1,
        note_id=1,
        note=note,
        direction="front_back",
        template_ord=0,
        question_template="{{cloze:Text}}",
        answer_template="{{FrontSide}}<br>{{cloze:Text}}",
    )

    assert card_question(card) == "Capital is [city]"
    assert card_answer(card) == "Capital is [city]\nCapital is Paris"
