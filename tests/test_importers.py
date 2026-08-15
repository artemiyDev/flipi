import json
import sqlite3
import zipfile
from pathlib import Path

from bot.services.apkg_importer import parse_apkg_cards, parse_apkg_media, parse_apkg_notes
from bot.services.importers import decode_text_payload, parse_text_cards


def test_parse_text_cards_csv_with_tags() -> None:
    rows = parse_text_cards("front,back,tag1 tag2\nfront2,back2,\n")

    assert rows == [
        ("front", "back", ["tag1", "tag2"], False),
        ("front2", "back2", [], False),
    ]


def test_parse_text_cards_tsv() -> None:
    rows = parse_text_cards("front\tback\ttag\n")

    assert rows == [("front", "back", ["tag"], False)]


def test_parse_text_cards_reverse_column() -> None:
    rows = parse_text_cards("front\tback\ttag\treverse\n")

    assert rows == [("front", "back", ["tag"], True)]


def test_decode_text_payload_cp1251() -> None:
    assert decode_text_payload("вопрос\tответ".encode("cp1251")) == "вопрос\tответ"


def test_parse_apkg_basic_card(tmp_path: Path) -> None:
    collection = tmp_path / "collection.anki2"
    conn = sqlite3.connect(collection)
    conn.execute("CREATE TABLE col (models text)")
    conn.execute("CREATE TABLE notes (id integer primary key, mid integer, flds text, tags text)")
    conn.execute("CREATE TABLE cards (id integer primary key, nid integer, did integer, ord integer)")
    conn.execute("INSERT INTO col(models) VALUES (?)", (json.dumps({"1": {"type": 0, "tmpls": []}}),))
    conn.execute(
        "INSERT INTO notes(id, mid, flds, tags) VALUES (1, 1, ?, ?)",
        ("Question\x1fAnswer", "tag1 tag2"),
    )
    conn.execute("INSERT INTO cards(id, nid, did, ord) VALUES (1, 1, 1, 0)")
    conn.commit()
    conn.close()

    package = tmp_path / "deck.apkg"
    with zipfile.ZipFile(package, "w") as archive:
        archive.write(collection, "collection.anki2")

    cards = parse_apkg_cards(package.read_bytes())

    assert len(cards) == 1
    assert cards[0].front == "Question"
    assert cards[0].back == "Answer"
    assert cards[0].tags == ["tag1", "tag2"]


def test_parse_apkg_template_snapshot(tmp_path: Path) -> None:
    collection = tmp_path / "collection.anki2"
    conn = sqlite3.connect(collection)
    conn.execute("CREATE TABLE col (models text)")
    conn.execute("CREATE TABLE notes (id integer primary key, mid integer, flds text, tags text)")
    conn.execute("CREATE TABLE cards (id integer primary key, nid integer, did integer, ord integer)")
    model = {
        "1": {
            "name": "Vocabulary",
            "type": 0,
            "flds": [{"name": "Word"}, {"name": "Meaning"}],
            "tmpls": [{"name": "Card 1", "qfmt": "{{Word}}", "afmt": "{{FrontSide}}<br>{{Meaning}}"}],
        }
    }
    conn.execute("INSERT INTO col(models) VALUES (?)", (json.dumps(model),))
    conn.execute("INSERT INTO notes(id, mid, flds, tags) VALUES (1, 1, ?, '')", ("bonjour\x1fhello",))
    conn.execute("INSERT INTO cards(id, nid, did, ord) VALUES (1, 1, 1, 0)")
    conn.commit()
    conn.close()

    package = tmp_path / "deck.apkg"
    with zipfile.ZipFile(package, "w") as archive:
        archive.write(collection, "collection.anki2")

    cards = parse_apkg_cards(package.read_bytes())

    assert cards[0].front == "bonjour"
    assert cards[0].back == "bonjour\nhello"
    assert cards[0].note_type == "Vocabulary"
    assert cards[0].fields == {"Word": "bonjour", "Meaning": "hello"}
    assert cards[0].template_name == "Card 1"
    assert cards[0].question_template == "{{Word}}"


def test_parse_apkg_groups_cards_by_note(tmp_path: Path) -> None:
    collection = tmp_path / "collection.anki2"
    conn = sqlite3.connect(collection)
    conn.execute("CREATE TABLE col (models text)")
    conn.execute("CREATE TABLE notes (id integer primary key, mid integer, flds text, tags text)")
    conn.execute("CREATE TABLE cards (id integer primary key, nid integer, did integer, ord integer)")
    model = {
        "1": {
            "name": "Vocabulary",
            "type": 0,
            "flds": [{"name": "Word"}, {"name": "Meaning"}],
            "tmpls": [
                {"name": "Forward", "qfmt": "{{Word}}", "afmt": "{{FrontSide}}<br>{{Meaning}}"},
                {"name": "Reverse", "qfmt": "{{Meaning}}", "afmt": "{{FrontSide}}<br>{{Word}}"},
            ],
        }
    }
    conn.execute("INSERT INTO col(models) VALUES (?)", (json.dumps(model),))
    conn.execute("INSERT INTO notes(id, mid, flds, tags) VALUES (1, 1, ?, 'tag')", ("bonjour\x1fhello",))
    conn.execute("INSERT INTO cards(id, nid, did, ord) VALUES (1, 1, 1, 0)")
    conn.execute("INSERT INTO cards(id, nid, did, ord) VALUES (2, 1, 1, 1)")
    conn.commit()
    conn.close()

    package = tmp_path / "deck.apkg"
    with zipfile.ZipFile(package, "w") as archive:
        archive.write(collection, "collection.anki2")

    notes = parse_apkg_notes(package.read_bytes())
    cards = parse_apkg_cards(package.read_bytes())

    assert len(notes) == 1
    assert len(notes[0].cards) == 2
    assert len(cards) == 2
    assert notes[0].cards[0].template_name == "Forward"
    assert notes[0].cards[1].template_name == "Reverse"


def test_parse_apkg_reads_note_guid(tmp_path: Path) -> None:
    collection = tmp_path / "collection.anki2"
    conn = sqlite3.connect(collection)
    conn.execute("CREATE TABLE col (models text)")
    conn.execute("CREATE TABLE notes (id integer primary key, guid text, mid integer, flds text, tags text)")
    conn.execute("CREATE TABLE cards (id integer primary key, nid integer, did integer, ord integer)")
    conn.execute("INSERT INTO col(models) VALUES (?)", (json.dumps({"1": {"type": 0, "tmpls": []}}),))
    conn.execute(
        "INSERT INTO notes(id, guid, mid, flds, tags) VALUES (1, 'stable-guid', 1, ?, '')",
        ("Question\x1fAnswer",),
    )
    conn.execute("INSERT INTO cards(id, nid, did, ord) VALUES (1, 1, 1, 0)")
    conn.commit()
    conn.close()

    package = tmp_path / "deck.apkg"
    with zipfile.ZipFile(package, "w") as archive:
        archive.write(collection, "collection.anki2")

    notes = parse_apkg_notes(package.read_bytes())

    assert notes[0].guid == "stable-guid"


def test_parse_apkg_deck_name(tmp_path: Path) -> None:
    collection = tmp_path / "collection.anki2"
    conn = sqlite3.connect(collection)
    conn.execute("CREATE TABLE col (models text, decks text)")
    conn.execute("CREATE TABLE notes (id integer primary key, mid integer, flds text, tags text)")
    conn.execute("CREATE TABLE cards (id integer primary key, nid integer, did integer, ord integer)")
    conn.execute(
        "INSERT INTO col(models, decks) VALUES (?, ?)",
        (
            json.dumps({"1": {"type": 0, "tmpls": []}}),
            json.dumps({"42": {"name": "Languages::French"}}),
        ),
    )
    conn.execute("INSERT INTO notes(id, mid, flds, tags) VALUES (1, 1, ?, '')", ("Bonjour\x1fHello",))
    conn.execute("INSERT INTO cards(id, nid, did, ord) VALUES (1, 1, 42, 0)")
    conn.commit()
    conn.close()

    package = tmp_path / "deck.apkg"
    with zipfile.ZipFile(package, "w") as archive:
        archive.write(collection, "collection.anki2")

    cards = parse_apkg_cards(package.read_bytes())

    assert cards[0].deck_name == "Languages::French"


def test_parse_apkg_media(tmp_path: Path) -> None:
    package = tmp_path / "media.apkg"
    with zipfile.ZipFile(package, "w") as archive:
        archive.writestr("media", json.dumps({"0": "image.png"}))
        archive.writestr("0", b"png-bytes")

    media = parse_apkg_media(package.read_bytes())

    assert len(media) == 1
    assert media[0].original_name == "image.png"
    assert media[0].content == b"png-bytes"
    assert len(media[0].sha256) == 64


def test_parse_apkg_cloze_card(tmp_path: Path) -> None:
    collection = tmp_path / "collection.anki2"
    conn = sqlite3.connect(collection)
    conn.execute("CREATE TABLE col (models text)")
    conn.execute("CREATE TABLE notes (id integer primary key, mid integer, flds text, tags text)")
    conn.execute("CREATE TABLE cards (id integer primary key, nid integer, did integer, ord integer)")
    model = {"1": {"type": 1, "tmpls": [{"qfmt": "{{cloze:Text}}"}]}}
    conn.execute("INSERT INTO col(models) VALUES (?)", (json.dumps(model),))
    conn.execute(
        "INSERT INTO notes(id, mid, flds, tags) VALUES (1, 1, ?, '')",
        ("Capital is {{c1::Paris::city}}\x1fExtra",),
    )
    conn.execute("INSERT INTO cards(id, nid, did, ord) VALUES (1, 1, 1, 0)")
    conn.commit()
    conn.close()

    package = tmp_path / "cloze.apkg"
    with zipfile.ZipFile(package, "w") as archive:
        archive.write(collection, "collection.anki2")

    cards = parse_apkg_cards(package.read_bytes())

    assert cards[0].front == "Capital is [city]"
    assert cards[0].back == "Capital is Paris\n\nExtra"


def test_parse_apkg_preserves_img_reference(tmp_path: Path) -> None:
    collection = tmp_path / "collection.anki2"
    conn = sqlite3.connect(collection)
    conn.execute("CREATE TABLE col (models text)")
    conn.execute("CREATE TABLE notes (id integer primary key, mid integer, flds text, tags text)")
    conn.execute("CREATE TABLE cards (id integer primary key, nid integer, did integer, ord integer)")
    conn.execute("INSERT INTO col(models) VALUES (?)", (json.dumps({"1": {"type": 0, "tmpls": []}}),))
    conn.execute(
        "INSERT INTO notes(id, mid, flds, tags) VALUES (1, 1, ?, '')",
        ('<img src="image.png">\x1fAnswer',),
    )
    conn.execute("INSERT INTO cards(id, nid, did, ord) VALUES (1, 1, 1, 0)")
    conn.commit()
    conn.close()

    package = tmp_path / "deck.apkg"
    with zipfile.ZipFile(package, "w") as archive:
        archive.write(collection, "collection.anki2")

    cards = parse_apkg_cards(package.read_bytes())

    assert cards[0].front == "[media:image.png]"
