import html
import hashlib
import json
import re
import sqlite3
import tempfile
import zipfile
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path


FIELD_SEPARATOR = "\x1f"
CLOZE_RE = re.compile(r"{{c(\d+)::(.*?)(?:::(.*?))?}}", flags=re.DOTALL)
TAG_RE = re.compile(r"<[^>]+>")
IMG_RE = re.compile(r"""(?is)<img\b[^>]*\bsrc=["']?([^"'\s>]+)["']?[^>]*>""")


@dataclass(frozen=True)
class ImportedCard:
    front: str
    back: str
    tags: list[str]
    deck_name: str | None = None
    create_reverse: bool = False
    note_type: str = "basic"
    anki_model_id: str | None = None
    fields: dict[str, str] | None = None
    template_name: str | None = None
    template_ord: int = 0
    question_template: str | None = None
    answer_template: str | None = None


@dataclass(frozen=True)
class ImportedNote:
    front: str
    back: str
    extra: str | None
    tags: list[str]
    note_type: str
    guid: str | None
    anki_model_id: str | None
    css: str | None
    fields: dict[str, str]
    deck_name: str | None
    cards: list[ImportedCard]


@dataclass(frozen=True)
class ImportedMedia:
    original_name: str
    content: bytes
    sha256: str


def parse_apkg_cards(payload: bytes) -> list[ImportedCard]:
    return [card for note in parse_apkg_notes(payload) for card in note.cards]


def parse_apkg_notes(payload: bytes) -> list[ImportedNote]:
    with zipfile.ZipFile(BytesIO(payload)) as package:
        collection_name = _find_collection_name(package)
        collection_bytes = package.read(collection_name)

    with tempfile.TemporaryDirectory() as tmp_dir:
        db_path = Path(tmp_dir) / "collection.anki"
        db_path.write_bytes(collection_bytes)
        return _read_collection_notes(db_path)


def parse_apkg_media(payload: bytes) -> list[ImportedMedia]:
    with zipfile.ZipFile(BytesIO(payload)) as package:
        if "media" not in package.namelist():
            return []
        media_map = json.loads(package.read("media").decode("utf-8"))
        media_files: list[ImportedMedia] = []
        for member_name, original_name in media_map.items():
            if member_name not in package.namelist() or not original_name:
                continue
            content = package.read(member_name)
            if not content:
                continue
            media_files.append(
                ImportedMedia(
                    original_name=str(original_name),
                    content=content,
                    sha256=hashlib.sha256(content).hexdigest(),
                )
            )
        return media_files


def _find_collection_name(package: zipfile.ZipFile) -> str:
    candidates = (
        "collection.anki21b",
        "collection.anki21",
        "collection.anki2",
    )
    names = set(package.namelist())
    for candidate in candidates:
        if candidate in names:
            return candidate
    raise ValueError("APKG package does not contain a supported Anki collection database.")


def _read_collection_notes(db_path: Path) -> list[ImportedNote]:
    conn = sqlite3.connect(db_path)
    try:
        conn.row_factory = sqlite3.Row
        models = _load_models(conn)
        decks = _load_decks(conn)
        note_columns = {column[1] for column in conn.execute("PRAGMA table_info(notes)")}
        guid_column = "n.guid" if "guid" in note_columns else "NULL"
        rows = conn.execute(
            """
            SELECT n.id AS note_id, """
            + guid_column
            + """ AS guid, c.ord, c.did, n.mid, n.flds, n.tags
            FROM cards c
            JOIN notes n ON n.id = c.nid
            ORDER BY n.id, c.ord, c.id
            """
        ).fetchall()
    finally:
        conn.close()

    notes: dict[int, ImportedNote] = {}
    for row in rows:
        note_id = int(row["note_id"])
        raw_fields = row["flds"].split(FIELD_SEPARATOR)
        if not raw_fields:
            continue
        model = models.get(str(row["mid"]), {})
        model_css = model.get("css")
        fields = _field_dict(model, raw_fields)
        deck_name = decks.get(str(row["did"]))
        tags = [tag for tag in row["tags"].split() if tag]
        imported = _card_from_fields(
            fields,
            int(row["ord"]),
            str(row["mid"]),
            model,
            tags,
            deck_name,
        )
        if imported is not None:
            existing = notes.get(note_id)
            if existing is None:
                notes[note_id] = ImportedNote(
                    front=imported.front,
                    back=imported.back,
                    extra=fields.get("Extra") or None,
                    tags=tags,
                    note_type=imported.note_type,
                    guid=str(row["guid"]) if row["guid"] is not None else None,
                    anki_model_id=imported.anki_model_id,
                    css=str(model_css) if isinstance(model_css, str) else None,
                    fields=imported.fields or {},
                    deck_name=deck_name,
                    cards=[imported],
                )
            else:
                notes[note_id] = ImportedNote(
                    front=existing.front,
                    back=existing.back,
                    extra=existing.extra,
                    tags=existing.tags,
                    note_type=existing.note_type,
                    guid=existing.guid,
                    anki_model_id=existing.anki_model_id,
                    css=existing.css,
                    fields=existing.fields,
                    deck_name=existing.deck_name,
                    cards=[*existing.cards, imported],
                )
    return list(notes.values())


def _load_models(conn: sqlite3.Connection) -> dict:
    row = conn.execute("SELECT models FROM col LIMIT 1").fetchone()
    if row is None:
        return {}
    return json.loads(row["models"])


def _load_decks(conn: sqlite3.Connection) -> dict[str, str]:
    try:
        row = conn.execute("SELECT decks FROM col LIMIT 1").fetchone()
    except sqlite3.OperationalError:
        return {}
    if row is None:
        return {}
    raw_decks = json.loads(row["decks"])
    return {
        str(deck_id): str(deck.get("name") or "Imported APKG")
        for deck_id, deck in raw_decks.items()
    }


def _card_from_fields(
    fields: dict[str, str],
    order: int,
    model_id: str,
    model: dict,
    tags: list[str],
    deck_name: str | None,
) -> ImportedCard | None:
    templates = model.get("tmpls") or []
    template = templates[order] if order < len(templates) else {}
    qfmt = template.get("qfmt")
    afmt = template.get("afmt")
    template_name = template.get("name")
    note_type = str(model.get("name") or "basic")

    if _is_cloze_model(model):
        cloze_no = order + 1
        front = _render_template(qfmt or "{{cloze:Text}}", fields, cloze_no=cloze_no)
        back = _render_template(
            afmt or "{{cloze:Text}}",
            fields,
            cloze_no=cloze_no,
            front_side=front,
            show_cloze_answer=True,
        )
        if not afmt:
            extra = "\n".join(value for key, value in fields.items() if key != "Text" and value)
            if extra:
                back = f"{back}\n\n{extra}"
        return _validated_card(
            front,
            back,
            tags,
            deck_name,
            note_type=note_type,
            anki_model_id=model_id,
            fields=fields,
            template_name=template_name,
            template_ord=order,
            question_template=qfmt,
            answer_template=afmt,
        )

    field_values = list(fields.values())
    if len(field_values) < 2:
        return None
    if qfmt or afmt:
        front = _render_template(qfmt or "{{Front}}", fields)
        back = _render_template(afmt or "{{Back}}", fields, front_side=front)
        return _validated_card(
            front,
            back,
            tags,
            deck_name,
            note_type=note_type,
            anki_model_id=model_id,
            fields=fields,
            template_name=template_name,
            template_ord=order,
            question_template=qfmt,
            answer_template=afmt,
        )

    if order == 1:
        return _validated_card(
            field_values[1],
            field_values[0],
            tags,
            deck_name,
            note_type=note_type,
            anki_model_id=model_id,
            fields=fields,
            template_name=template_name,
            template_ord=order,
        )
    return _validated_card(
        field_values[0],
        field_values[1],
        tags,
        deck_name,
        note_type=note_type,
        anki_model_id=model_id,
        fields=fields,
        template_name=template_name,
        template_ord=order,
    )


def _validated_card(
    front: str,
    back: str,
    tags: list[str],
    deck_name: str | None,
    note_type: str = "basic",
    anki_model_id: str | None = None,
    fields: dict[str, str] | None = None,
    template_name: str | None = None,
    template_ord: int = 0,
    question_template: str | None = None,
    answer_template: str | None = None,
) -> ImportedCard | None:
    front = _clean_field(front)
    back = _clean_field(back)
    if not front or not back:
        return None
    cleaned_fields = {key: _clean_field(value) for key, value in (fields or {}).items()}
    return ImportedCard(
        front=front,
        back=back,
        tags=tags,
        deck_name=deck_name,
        note_type=note_type,
        anki_model_id=anki_model_id,
        fields=cleaned_fields,
        template_name=template_name,
        template_ord=template_ord,
        question_template=question_template,
        answer_template=answer_template,
    )


def _is_cloze_model(model: dict) -> bool:
    if model.get("type") == 1:
        return True
    templates = json.dumps(model.get("tmpls", []))
    return "{{cloze:" in templates


def _render_cloze(text: str, cloze_no: int, show_answer: bool) -> str:
    def replace(match: re.Match) -> str:
        current_no = int(match.group(1))
        answer = match.group(2)
        hint = match.group(3)
        if current_no != cloze_no:
            return answer
        if show_answer:
            return answer
        return f"[{hint or '...'}]"

    return _clean_field(CLOZE_RE.sub(replace, text))


def _field_dict(model: dict, raw_fields: list[str]) -> dict[str, str]:
    configured_fields = model.get("flds") or []
    names = [str(field.get("name") or f"Field{idx + 1}") for idx, field in enumerate(configured_fields)]
    if not names and _is_cloze_model(model):
        names = ["Text", "Extra"]
    if len(names) < len(raw_fields):
        names.extend(f"Field{idx + 1}" for idx in range(len(names), len(raw_fields)))
    return {name: raw_fields[idx] for idx, name in enumerate(names[: len(raw_fields)])}


def _render_template(
    template: str,
    fields: dict[str, str],
    cloze_no: int | None = None,
    front_side: str = "",
    show_cloze_answer: bool = False,
) -> str:
    rendered = template or ""
    rendered = _render_conditionals(rendered, fields)
    rendered = rendered.replace("{{FrontSide}}", front_side)

    def replace_cloze(match: re.Match) -> str:
        field_name = match.group(1).strip()
        text = fields.get(field_name, "")
        if cloze_no is None:
            return text
        return _render_cloze(text, cloze_no, show_answer=show_cloze_answer)

    rendered = re.sub(r"{{cloze:([^}]+)}}", replace_cloze, rendered)

    def replace_field(match: re.Match) -> str:
        expression = match.group(1).strip()
        if expression.startswith("type:"):
            expression = expression.removeprefix("type:").strip()
        return fields.get(expression, "")

    return re.sub(r"{{([^#/^][^}]*)}}", replace_field, rendered)


def _render_conditionals(template: str, fields: dict[str, str]) -> str:
    rendered = template
    for field_name, value in fields.items():
        escaped_name = re.escape(field_name)
        if value:
            rendered = re.sub(r"{{#" + escaped_name + r"}}(.*?){{/" + escaped_name + r"}}", r"\1", rendered, flags=re.DOTALL)
            rendered = re.sub(r"{{\^" + escaped_name + r"}}(.*?){{/" + escaped_name + r"}}", "", rendered, flags=re.DOTALL)
        else:
            rendered = re.sub(r"{{#" + escaped_name + r"}}(.*?){{/" + escaped_name + r"}}", "", rendered, flags=re.DOTALL)
            rendered = re.sub(r"{{\^" + escaped_name + r"}}(.*?){{/" + escaped_name + r"}}", r"\1", rendered, flags=re.DOTALL)
    return rendered


def _clean_field(value: str) -> str:
    value = re.sub(r"(?is)<(script|style).*?</\1>", "", value)
    value = re.sub(r"(?i)<br\s*/?>", "\n", value)
    value = IMG_RE.sub(lambda match: f"[media:{match.group(1)}]", value)
    value = TAG_RE.sub("", value)
    return html.unescape(value).replace("\xa0", " ").strip()
