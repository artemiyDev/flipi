import asyncio

from sqlalchemy import select

from bot.models import Card, Note
from test_api import build_app, post_request, request, signed_init_data


def test_cards_api_creates_cloze_note_and_renders_study_card(session_factory, monkeypatch) -> None:
    app = build_app(session_factory, monkeypatch)
    headers = {"X-Telegram-Init-Data": signed_init_data()}
    deck_id = post_request(app, "/api/decks", {"name": "Cloze"}, headers).json()["id"]

    created = post_request(
        app,
        "/api/cards",
        {
            "deck_id": deck_id,
            "type": "cloze",
            "front": "Capital of France is {{c1::Paris}}; country is {{c2::France}}.",
            "back": "Geography",
            "tags": ["places"],
            "reverse": True,
        },
        headers,
    )

    async def records() -> tuple[Note, list[Card]]:
        async with session_factory() as session:
            note = await session.get(Note, created.json()["note_id"])
            assert note is not None
            result = await session.execute(
                select(Card).where(Card.note_id == note.id).order_by(Card.template_ord)
            )
            return note, list(result.scalars())

    note, cards = asyncio.run(records())
    study = request(app, f"/api/study/next?deck_id={deck_id}", headers)

    assert created.status_code == 201
    assert created.json()["cards_created"] == 2
    assert note.note_type == "Cloze"
    assert note.fields == {
        "Text": "Capital of France is {{c1::Paris}}; country is {{c2::France}}.",
        "Extra": "Geography",
    }
    assert [card.template_ord for card in cards] == [0, 1]
    assert all(card.question_template == "{{cloze:Text}}" for card in cards)
    assert study.status_code == 200
    assert "[...]" in study.json()["question_html"]
    assert "France" in study.json()["question_html"]
    assert "Paris" in study.json()["answer_html"]
    assert "Geography" in study.json()["answer_html"]


def test_cards_api_creates_one_cloze_card_per_distinct_number(session_factory, monkeypatch) -> None:
    app = build_app(session_factory, monkeypatch)
    headers = {"X-Telegram-Init-Data": signed_init_data()}
    deck_id = post_request(app, "/api/decks", {"name": "Numbers"}, headers).json()["id"]
    created = post_request(
        app,
        "/api/cards",
        {
            "deck_id": deck_id,
            "type": "cloze",
            "front": "{{c1::one}}, {{c1::another one}}, {{c3::three}}",
        },
        headers,
    )

    async def template_orders() -> list[int]:
        async with session_factory() as session:
            result = await session.execute(
                select(Card.template_ord)
                .where(Card.note_id == created.json()["note_id"])
                .order_by(Card.template_ord)
            )
            return list(result.scalars())

    assert created.status_code == 201
    assert created.json()["cards_created"] == 2
    assert asyncio.run(template_orders()) == [0, 2]


def test_cards_api_rejects_cloze_without_deletion(session_factory, monkeypatch) -> None:
    app = build_app(session_factory, monkeypatch)
    headers = {"X-Telegram-Init-Data": signed_init_data()}
    deck_id = post_request(app, "/api/decks", {"name": "Validation"}, headers).json()["id"]

    response = post_request(
        app,
        "/api/cards",
        {"deck_id": deck_id, "type": "cloze", "front": "No deletion here"},
        headers,
    )

    assert response.status_code == 422
    assert response.json()["detail"] == "Добавьте хотя бы один пропуск"
