import html
import re
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from bot.models import Card, DailyStudyCounter, Deck, Note, User
from bot.services.scheduler import new_fsrs_card_json
from bot.services.timezones import user_today

CLOZE_RE = re.compile(r"{{c(\d+)::(.*?)(?:::(.*?))?}}", flags=re.DOTALL)
TAG_RE = re.compile(r"<[^>]+>")
IMG_RE = re.compile(r"""(?is)<img\b[^>]*\bsrc=["']?([^"'\s>]+)["']?[^>]*>""")


@dataclass(frozen=True)
class BrowserQuery:
    text_terms: list[str] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)
    states: list[str] = field(default_factory=list)
    flags: list[str] = field(default_factory=list)
    decks: list[str] = field(default_factory=list)
    is_due: bool = False
    is_suspended: bool = False
    is_buried: bool = False
    has_flag: bool = False


def card_question(card: Card) -> str:
    if card.question_template and card.note.fields:
        rendered = _render_template(
            card.question_template,
            card.note.fields,
            cloze_no=card.template_ord + 1,
            show_cloze_answer=False,
        )
        if rendered:
            return rendered
    if card.direction == "back_front":
        return card.note.back
    return card.note.front


def card_answer(card: Card) -> str:
    if card.answer_template and card.note.fields:
        rendered = _render_template(
            card.answer_template,
            card.note.fields,
            cloze_no=card.template_ord + 1,
            front_side=card_question(card),
            show_cloze_answer=True,
        )
        if rendered:
            return rendered
    if card.direction == "back_front":
        return card.note.front
    return card.note.back


def _render_template(
    template: str,
    fields: dict,
    cloze_no: int | None = None,
    front_side: str = "",
    show_cloze_answer: bool = False,
) -> str:
    rendered = template or ""
    rendered = _render_conditionals(rendered, fields)
    rendered = rendered.replace("{{FrontSide}}", front_side)

    def replace_cloze(match: re.Match) -> str:
        field_name = match.group(1).strip()
        text = str(fields.get(field_name, ""))
        if cloze_no is None:
            return text
        return _render_cloze(text, cloze_no, show_answer=show_cloze_answer)

    rendered = re.sub(r"{{cloze:([^}]+)}}", replace_cloze, rendered)

    def replace_field(match: re.Match) -> str:
        expression = match.group(1).strip()
        if expression.startswith("type:"):
            expression = expression.removeprefix("type:").strip()
        return str(fields.get(expression, ""))

    return _clean_rendered_text(re.sub(r"{{([^#/^][^}]*)}}", replace_field, rendered))


def _render_conditionals(template: str, fields: dict) -> str:
    rendered = template
    for field_name, value in fields.items():
        escaped_name = re.escape(str(field_name))
        if value:
            rendered = re.sub(
                r"{{#" + escaped_name + r"}}(.*?){{/" + escaped_name + r"}}",
                r"\1",
                rendered,
                flags=re.DOTALL,
            )
            rendered = re.sub(
                r"{{\^" + escaped_name + r"}}(.*?){{/" + escaped_name + r"}}",
                "",
                rendered,
                flags=re.DOTALL,
            )
        else:
            rendered = re.sub(
                r"{{#" + escaped_name + r"}}(.*?){{/" + escaped_name + r"}}",
                "",
                rendered,
                flags=re.DOTALL,
            )
            rendered = re.sub(
                r"{{\^" + escaped_name + r"}}(.*?){{/" + escaped_name + r"}}",
                r"\1",
                rendered,
                flags=re.DOTALL,
            )
    return rendered


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

    return CLOZE_RE.sub(replace, text)


def _clean_rendered_text(value: str) -> str:
    value = re.sub(r"(?is)<(script|style).*?</\1>", "", value)
    value = re.sub(r"(?i)<br\s*/?>", "\n", value)
    value = IMG_RE.sub(lambda match: f"[media:{match.group(1)}]", value)
    value = TAG_RE.sub("", value)
    return html.unescape(value).replace("\xa0", " ").strip()


async def create_basic_note(
    session: AsyncSession,
    user: User,
    deck: Deck,
    front: str,
    back: str,
    tags: list[str] | None = None,
    create_reverse: bool = False,
    source: str | None = None,
    note_type: str = "basic",
    anki_model_id: str | None = None,
    fields: dict | None = None,
    template_name: str | None = None,
    template_ord: int = 0,
    question_template: str | None = None,
    answer_template: str | None = None,
) -> Note:
    now = datetime.now(UTC)
    note = Note(
        user_id=user.id,
        deck_id=deck.id,
        note_type=note_type,
        anki_model_id=anki_model_id,
        fields=fields,
        front=front.strip(),
        back=back.strip(),
        tags=tags or [],
        source=source,
    )
    session.add(note)
    await session.flush()

    session.add(
        Card(
            user_id=user.id,
            deck_id=deck.id,
            note_id=note.id,
            direction="front_back",
            template_name=template_name,
            template_ord=template_ord,
            question_template=question_template,
            answer_template=answer_template,
            due_at=now,
            state="new",
            fsrs_data=new_fsrs_card_json(),
        )
    )
    if create_reverse:
        session.add(
            Card(
                user_id=user.id,
                deck_id=deck.id,
                note_id=note.id,
                direction="back_front",
                template_name="Reverse",
                template_ord=1,
                due_at=now,
                state="new",
                fsrs_data=new_fsrs_card_json(),
            )
        )

    await session.commit()
    await session.refresh(note)
    return note


async def create_note_with_cards(
    session: AsyncSession,
    user: User,
    deck: Deck,
    front: str,
    back: str,
    tags: list[str] | None,
    note_type: str,
    anki_model_id: str | None,
    fields: dict | None,
    source: str | None,
    card_specs: list[dict],
) -> Note:
    now = datetime.now(UTC)
    note = Note(
        user_id=user.id,
        deck_id=deck.id,
        note_type=note_type,
        anki_model_id=anki_model_id,
        fields=fields,
        front=front.strip(),
        back=back.strip(),
        tags=tags or [],
        source=source,
    )
    session.add(note)
    await session.flush()

    for index, spec in enumerate(card_specs):
        card_deck = spec.get("deck") or deck
        session.add(
            Card(
                user_id=user.id,
                deck_id=card_deck.id,
                note_id=note.id,
                direction=spec.get("direction") or "front_back",
                template_name=spec.get("template_name"),
                template_ord=int(spec.get("template_ord", index)),
                question_template=spec.get("question_template"),
                answer_template=spec.get("answer_template"),
                due_at=now,
                state="new",
                fsrs_data=new_fsrs_card_json(),
            )
        )

    await session.commit()
    await session.refresh(note)
    return note


async def note_exists(
    session: AsyncSession,
    user: User,
    deck: Deck,
    front: str,
    back: str,
) -> bool:
    result = await session.execute(
        select(Note.id)
        .where(
            Note.user_id == user.id,
            Note.deck_id == deck.id,
            Note.front == front.strip(),
            Note.back == back.strip(),
        )
        .limit(1)
    )
    return result.scalar_one_or_none() is not None


async def get_card(session: AsyncSession, user: User, card_id: int) -> Card | None:
    result = await session.execute(
        select(Card)
        .where(Card.id == card_id, Card.user_id == user.id)
        .options(selectinload(Card.note), selectinload(Card.deck))
    )
    return result.scalar_one_or_none()


async def get_note(session: AsyncSession, user: User, note_id: int) -> Note | None:
    result = await session.execute(
        select(Note)
        .where(Note.id == note_id, Note.user_id == user.id)
        .options(selectinload(Note.cards), selectinload(Note.deck))
    )
    return result.scalar_one_or_none()


async def get_next_due_card(
    session: AsyncSession,
    deck: Deck,
    timezone_name: str = "UTC",
) -> Card | None:
    now = datetime.now(UTC)
    today = user_today(timezone_name)
    counter = await get_daily_study_counter(session, deck, timezone_name)
    can_show_new = counter.new_seen < deck.new_cards_per_day
    can_show_review = counter.reviews_done < deck.reviews_per_day
    base_filters = [
        Card.deck_id == deck.id,
        Card.user_id == deck.user_id,
        Card.suspended.is_(False),
        or_(Card.buried_until.is_(None), Card.buried_until < today),
        Card.due_at <= now,
    ]

    priorities = [Card.state.in_(["learning", "relearning"])]
    if can_show_review:
        priorities.append(Card.state == "review")
    if can_show_new:
        priorities.append(Card.state == "new")
    for priority in priorities:
        result = await session.execute(
            select(Card)
            .where(*base_filters, priority)
            .order_by(Card.due_at.asc(), Card.id.asc())
            .options(selectinload(Card.note), selectinload(Card.deck))
            .limit(1)
        )
        card = result.scalar_one_or_none()
        if card is not None:
            return card
    return None


async def get_next_review_ahead_card(
    session: AsyncSession,
    deck: Deck,
    timezone_name: str = "UTC",
) -> Card | None:
    now = datetime.now(UTC)
    today = user_today(timezone_name)
    result = await session.execute(
        select(Card)
        .where(
            Card.deck_id == deck.id,
            Card.user_id == deck.user_id,
            Card.suspended.is_(False),
            or_(Card.buried_until.is_(None), Card.buried_until < today),
            Card.state == "review",
            Card.due_at > now,
        )
        .order_by(Card.due_at.asc(), Card.id.asc())
        .options(selectinload(Card.note), selectinload(Card.deck))
        .limit(1)
    )
    return result.scalar_one_or_none()


async def get_next_new_card_without_limit(
    session: AsyncSession,
    deck: Deck,
    timezone_name: str = "UTC",
) -> Card | None:
    now = datetime.now(UTC)
    today = user_today(timezone_name)
    result = await session.execute(
        select(Card)
        .where(
            Card.deck_id == deck.id,
            Card.user_id == deck.user_id,
            Card.suspended.is_(False),
            or_(Card.buried_until.is_(None), Card.buried_until < today),
            Card.state == "new",
            Card.due_at <= now,
        )
        .order_by(Card.due_at.asc(), Card.id.asc())
        .options(selectinload(Card.note), selectinload(Card.deck))
        .limit(1)
    )
    return result.scalar_one_or_none()


async def get_daily_study_counter(
    session: AsyncSession,
    deck: Deck,
    timezone_name: str = "UTC",
) -> DailyStudyCounter:
    today = user_today(timezone_name)
    result = await session.execute(
        select(DailyStudyCounter).where(
            DailyStudyCounter.user_id == deck.user_id,
            DailyStudyCounter.deck_id == deck.id,
            DailyStudyCounter.study_date == today,
        )
    )
    counter = result.scalar_one_or_none()
    if counter is None:
        counter = DailyStudyCounter(
            user_id=deck.user_id,
            deck_id=deck.id,
            study_date=today,
            new_seen=0,
            reviews_done=0,
        )
        session.add(counter)
        await session.flush()
    return counter


async def increment_daily_counter(
    session: AsyncSession,
    card: Card,
    previous_state: str,
    timezone_name: str = "UTC",
) -> None:
    counter = await get_daily_study_counter(session, card.deck, timezone_name)
    if previous_state == "new":
        counter.new_seen += 1
    else:
        counter.reviews_done += 1


async def search_notes(session: AsyncSession, user: User, query: str, limit: int = 10) -> list[Note]:
    term = f"%{query.strip()}%"
    result = await session.execute(
        select(Note)
        .where(
            Note.user_id == user.id,
            or_(Note.front.ilike(term), Note.back.ilike(term), Note.extra.ilike(term)),
        )
        .options(selectinload(Note.cards), selectinload(Note.deck))
        .order_by(Note.updated_at.desc())
        .limit(limit)
    )
    return list(result.scalars())


async def search_cards(session: AsyncSession, user: User, query: str, limit: int = 20) -> list[Card]:
    now = datetime.now(UTC)
    today = user_today(user.timezone)
    filters = _build_card_query_filters(user, query, now, today)

    result = await session.execute(
        select(Card)
        .join(Card.note)
        .join(Card.deck)
        .where(*filters)
        .options(selectinload(Card.note), selectinload(Card.deck))
        .order_by(Card.due_at.asc(), Card.id.asc())
        .limit(limit)
    )
    return list(result.scalars())


async def get_next_due_card_by_query(
    session: AsyncSession,
    user: User,
    query: str,
) -> Card | None:
    now = datetime.now(UTC)
    today = user_today(user.timezone)
    base_filters = _build_card_query_filters(user, query, now, today)
    base_filters.extend(
        [
            Deck.is_archived.is_(False),
            Card.suspended.is_(False),
            or_(Card.buried_until.is_(None), Card.buried_until < today),
            Card.due_at <= now,
        ]
    )
    priorities = [
        Card.state.in_(["learning", "relearning"]),
        Card.state == "review",
        Card.state == "new",
    ]

    for priority in priorities:
        result = await session.execute(
            select(Card)
            .join(Card.note)
            .join(Card.deck)
            .where(*base_filters, priority)
            .options(selectinload(Card.note), selectinload(Card.deck))
            .order_by(Card.due_at.asc(), Card.id.asc())
            .limit(50)
        )
        for card in result.scalars():
            counter = await get_daily_study_counter(session, card.deck, user.timezone)
            if card.state == "new" and counter.new_seen >= card.deck.new_cards_per_day:
                continue
            if card.state == "review" and counter.reviews_done >= card.deck.reviews_per_day:
                continue
            return card
    return None


async def count_cards_by_query(session: AsyncSession, user: User, query: str) -> int:
    now = datetime.now(UTC)
    today = user_today(user.timezone)
    filters = _build_card_query_filters(user, query, now, today)
    result = await session.execute(
        select(func.count(Card.id))
        .join(Card.note)
        .join(Card.deck)
        .where(*filters)
    )
    return int(result.scalar_one())


async def count_due_cards_by_query(session: AsyncSession, user: User, query: str) -> int:
    now = datetime.now(UTC)
    today = user_today(user.timezone)
    filters = _build_card_query_filters(user, query, now, today)
    filters.extend(
        [
            Card.suspended.is_(False),
            or_(Card.buried_until.is_(None), Card.buried_until < today),
            Card.due_at <= now,
        ]
    )
    result = await session.execute(
        select(func.count(Card.id))
        .join(Card.note)
        .join(Card.deck)
        .where(*filters)
    )
    return int(result.scalar_one())


def _build_card_query_filters(user: User, query: str, now: datetime, today: date) -> list:
    filters = [Card.user_id == user.id, Deck.is_archived.is_(False)]
    parsed = parse_browser_query(query)

    for tag in parsed.tags:
        filters.append(Note.tags.contains([tag]))
    for state in parsed.states:
        filters.append(Card.state == state)
    for flag in parsed.flags:
        filters.append(Card.flag == flag)
    for deck in parsed.decks:
        filters.append(Deck.name.ilike(f"%{deck}%"))
    if parsed.is_due:
        filters.extend([Card.due_at <= now, Card.suspended.is_(False)])
    if parsed.is_suspended:
        filters.append(Card.suspended.is_(True))
    if parsed.is_buried:
        filters.append(Card.buried_until >= today)
    if parsed.has_flag:
        filters.append(Card.flag.is_not(None))

    for term in parsed.text_terms:
        pattern = f"%{term}%"
        filters.append(
            or_(
                Note.front.ilike(pattern),
                Note.back.ilike(pattern),
                Note.extra.ilike(pattern),
                Deck.name.ilike(pattern),
            )
        )
    return filters


def parse_browser_query(query: str) -> BrowserQuery:
    text_terms: list[str] = []
    tags: list[str] = []
    states: list[str] = []
    flags: list[str] = []
    decks: list[str] = []
    is_due = False
    is_suspended = False
    is_buried = False
    has_flag = False

    for token in query.split():
        key, _, value = token.partition(":")
        key = key.lower()
        value = value.strip()
        if key == "tag" and value:
            tags.append(value)
        elif key == "state" and value:
            states.append(value.lower())
        elif key == "flag" and value:
            flags.append(value.lower())
        elif key == "deck" and value:
            decks.append(value)
        elif key == "is" and value == "due":
            is_due = True
        elif key == "is" and value == "suspended":
            is_suspended = True
        elif key == "is" and value == "buried":
            is_buried = True
        elif key == "has" and value == "flag":
            has_flag = True
        elif token:
            text_terms.append(token)

    return BrowserQuery(
        text_terms=text_terms,
        tags=tags,
        states=states,
        flags=flags,
        decks=decks,
        is_due=is_due,
        is_suspended=is_suspended,
        is_buried=is_buried,
        has_flag=has_flag,
    )


async def update_note_field(
    session: AsyncSession,
    note: Note,
    field: str,
    value: str,
) -> None:
    sync_note_fields_for_edit(note, field, value)
    await session.commit()


def sync_note_fields_for_edit(note: Note, field: str, value: str) -> None:
    if field == "front":
        note.front = value.strip()
        _update_ordered_note_field(note, 0, value.strip())
    elif field == "back":
        note.back = value.strip()
        _update_ordered_note_field(note, 1, value.strip())
    elif field == "tags":
        note.tags = [tag for tag in value.replace(",", " ").split() if tag]
    else:
        raise ValueError(f"Unsupported note field: {field}")


def _update_ordered_note_field(note: Note, index: int, value: str) -> None:
    if not note.fields:
        return
    keys = list(note.fields.keys())
    if index >= len(keys):
        return
    note.fields = {**note.fields, keys[index]: value}


async def delete_note(session: AsyncSession, note: Note) -> None:
    await session.delete(note)
    await session.commit()


async def set_card_suspended(session: AsyncSession, card: Card, suspended: bool) -> None:
    card.suspended = suspended
    if suspended:
        card.buried_until = None
    await session.commit()


async def bury_card_until_tomorrow(
    session: AsyncSession,
    card: Card,
    timezone_name: str = "UTC",
) -> None:
    card.buried_until = user_today(timezone_name) + timedelta(days=1)
    await session.commit()


async def bury_sibling_cards(
    session: AsyncSession,
    card: Card,
    timezone_name: str = "UTC",
) -> None:
    tomorrow = user_today(timezone_name) + timedelta(days=1)
    result = await session.execute(
        select(Card).where(
            Card.note_id == card.note_id,
            Card.id != card.id,
            Card.suspended.is_(False),
        )
    )
    for sibling in result.scalars():
        sibling.buried_until = tomorrow


async def set_card_due_in_days(session: AsyncSession, card: Card, days: int) -> None:
    card.due_at = datetime.now(UTC) + timedelta(days=max(days, 0))
    card.state = "review"
    card.buried_until = None
    card.suspended = False
    await session.commit()


async def reset_card(session: AsyncSession, card: Card) -> None:
    card.due_at = datetime.now(UTC)
    card.state = "new"
    card.fsrs_data = new_fsrs_card_json()
    card.buried_until = None
    card.suspended = False
    await session.commit()


async def set_card_flag(session: AsyncSession, card: Card, flag: str | None) -> None:
    card.flag = flag
    await session.commit()
