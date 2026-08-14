from bot.models import Note
from bot.services.cards import sync_note_fields_for_edit


def test_sync_note_fields_for_front_back_edits() -> None:
    note = Note(
        id=1,
        user_id=1,
        deck_id=1,
        note_type="Vocabulary",
        fields={"Word": "bonjour", "Meaning": "hello"},
        front="bonjour",
        back="hello",
    )

    sync_note_fields_for_edit(note, "front", "salut")
    sync_note_fields_for_edit(note, "back", "hi")

    assert note.front == "salut"
    assert note.back == "hi"
    assert note.fields == {"Word": "salut", "Meaning": "hi"}
