from datetime import UTC, datetime

from collections.abc import Mapping
from typing import Any

from sqlalchemy import Select, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from bot.models import Card, Deck, Note, ReviewLog, User

DECK_OPTION_PRESETS = {
    "light": {
        "new_cards_per_day": 10,
        "reviews_per_day": 80,
        "desired_retention": 0.88,
        "learning_steps_minutes": [1, 10],
        "relearning_steps_minutes": [10],
        "maximum_interval_days": 36500,
        "enable_fuzzing": True,
    },
    "balanced": {
        "new_cards_per_day": 20,
        "reviews_per_day": 200,
        "desired_retention": 0.9,
        "learning_steps_minutes": [1, 10],
        "relearning_steps_minutes": [10],
        "maximum_interval_days": 36500,
        "enable_fuzzing": True,
    },
    "intense": {
        "new_cards_per_day": 50,
        "reviews_per_day": 500,
        "desired_retention": 0.92,
        "learning_steps_minutes": [1, 5, 15],
        "relearning_steps_minutes": [5, 15],
        "maximum_interval_days": 36500,
        "enable_fuzzing": True,
    },
    "exam": {
        "new_cards_per_day": 100,
        "reviews_per_day": 1000,
        "desired_retention": 0.95,
        "learning_steps_minutes": [1, 3, 10],
        "relearning_steps_minutes": [3, 10],
        "maximum_interval_days": 365,
        "enable_fuzzing": False,
    },
}

DECK_SETTINGS_FIELDS = frozenset(
    {
        "new_cards_per_day",
        "reviews_per_day",
        "desired_retention",
        "learning_steps_minutes",
        "relearning_steps_minutes",
        "maximum_interval_days",
        "bury_siblings",
        "enable_fuzzing",
    }
)


class DeckNameConflictError(ValueError):
    """Raised when a deck name is already used at the same hierarchy level."""


def validate_api_deck_name(name: str) -> str:
    normalized = name.strip()
    if not normalized:
        raise ValueError("Deck name must not be empty")
    if len(normalized) > 255:
        raise ValueError("Deck name must be at most 255 characters")
    if "::" in normalized:
        raise ValueError("Deck name must not contain ::")
    return normalized


def validate_deck_setting_value(field: str, value: Any) -> int | float | bool | list[int] | None:
    if field in {"learning_steps_minutes", "relearning_steps_minutes"}:
        if isinstance(value, str):
            try:
                value = [int(item.strip()) for item in value.strip().split(",") if item.strip()]
            except ValueError:
                return None
        if not isinstance(value, list) or len(value) > 6 or not value:
            return None
        if any(isinstance(item, bool) or not isinstance(item, int) for item in value):
            return None
        return value if all(1 <= item <= 1440 for item in value) else None

    if isinstance(value, str):
        value = value.strip().replace(",", ".")
        try:
            if field in {"new_cards_per_day", "reviews_per_day", "maximum_interval_days"}:
                value = int(value)
            elif field == "desired_retention":
                value = float(value)
        except ValueError:
            return None

    if field in {"new_cards_per_day", "reviews_per_day"}:
        return value if isinstance(value, int) and not isinstance(value, bool) and 0 <= value <= 5000 else None
    if field == "desired_retention":
        return value if isinstance(value, (int, float)) and not isinstance(value, bool) and 0.7 <= value <= 0.97 else None
    if field == "maximum_interval_days":
        return value if isinstance(value, int) and not isinstance(value, bool) and 1 <= value <= 36500 else None
    if field in {"bury_siblings", "enable_fuzzing"}:
        return value if isinstance(value, bool) else None
    return None


async def create_deck(
    session: AsyncSession,
    user: User,
    name: str,
    description: str | None = None,
    parent: Deck | None = None,
) -> Deck:
    deck = Deck(
        user_id=user.id,
        parent_id=parent.id if parent is not None else None,
        name=name.strip(),
        description=description,
    )
    session.add(deck)
    await session.commit()
    await session.refresh(deck)
    return deck


async def create_api_root_deck(
    session: AsyncSession,
    user: User,
    name: str,
    description: str | None = None,
) -> Deck:
    normalized_name = validate_api_deck_name(name)
    existing = await _get_deck_by_name(session, user, normalized_name)
    if existing is not None:
        raise DeckNameConflictError("Deck name already exists")
    try:
        return await create_deck(session, user, normalized_name, description)
    except IntegrityError as exc:
        await session.rollback()
        raise DeckNameConflictError("Deck name already exists") from exc


async def get_or_create_deck(
    session: AsyncSession,
    user: User,
    name: str,
    description: str | None = None,
) -> Deck:
    result = await session.execute(
        select(Deck).where(
            Deck.user_id == user.id,
            Deck.parent_id.is_(None),
            Deck.name == name.strip(),
        )
    )
    deck = result.scalar_one_or_none()
    if deck is not None:
        if deck.is_archived:
            deck.is_archived = False
            await session.commit()
        return deck
    return await create_deck(session, user, name, description)


async def resolve_apkg_deck(
    session: AsyncSession,
    user: User,
    anki_name: str,
    description: str | None = None,
) -> Deck:
    """Return the APKG target deck, creating its missing parent chain."""
    name = anki_name.strip()
    existing = await _get_deck_by_name(session, user, name)
    if existing is not None:
        if existing.is_archived:
            existing.is_archived = False
            await session.commit()
        return existing

    parent = None
    segments = name.split("::")
    for index, segment in enumerate(segments):
        parent = await get_or_create_child_deck(
            session,
            user,
            segment,
            parent,
            description if index == len(segments) - 1 else None,
        )
    return parent


async def _get_deck_by_name(
    session: AsyncSession,
    user: User,
    name: str,
) -> Deck | None:
    result = await session.execute(
        select(Deck).where(
            Deck.user_id == user.id,
            Deck.parent_id.is_(None),
            Deck.name == name,
        )
    )
    return result.scalar_one_or_none()


async def available_root_deck_name(
    session: AsyncSession,
    user: User,
    title: str,
    source_label: str,
) -> str:
    existing_names = set(
        (
            await session.execute(
                select(Deck.name).where(
                    Deck.user_id == user.id,
                    Deck.parent_id.is_(None),
                )
            )
        ).scalars()
    )
    if title not in existing_names:
        return title

    base_name = f"{title} ({source_label})"
    if base_name not in existing_names:
        return base_name

    suffix = 2
    while f"{base_name} {suffix}" in existing_names:
        suffix += 1
    return f"{base_name} {suffix}"


async def get_or_create_child_deck(
    session: AsyncSession,
    user: User,
    name: str,
    parent: Deck | None,
    description: str | None = None,
) -> Deck:
    parent_clause = Deck.parent_id.is_(None) if parent is None else Deck.parent_id == parent.id
    result = await session.execute(
        select(Deck).where(
            Deck.user_id == user.id,
            parent_clause,
            Deck.name == name.strip(),
        )
    )
    deck = result.scalar_one_or_none()
    if deck is not None:
        if deck.is_archived:
            deck.is_archived = False
            await session.commit()
        return deck
    return await create_deck(session, user, name, description, parent)


def deck_full_path(deck: Deck, decks_by_id: Mapping[int, Deck]) -> str:
    names = [deck.name]
    seen = {deck.id}
    parent_id = deck.parent_id
    while parent_id is not None and parent_id not in seen:
        parent = decks_by_id.get(parent_id)
        if parent is None:
            break
        names.append(parent.name)
        seen.add(parent.id)
        parent_id = parent.parent_id
    return "::".join(reversed(names))


async def list_user_decks(session: AsyncSession, user: User) -> list[Deck]:
    decks = await _list_all_user_decks(session, user)
    return [deck for deck in decks if not deck.is_archived]


async def list_archived_decks(session: AsyncSession, user: User) -> list[Deck]:
    decks = await _list_all_user_decks(session, user)
    return [deck for deck in decks if deck.is_archived]


async def list_all_user_decks(session: AsyncSession, user: User) -> list[Deck]:
    return await _list_all_user_decks(session, user)


async def list_user_deck_display_choices(
    session: AsyncSession,
    user: User,
) -> list[tuple[int, str]]:
    decks = await _list_all_user_decks(session, user)
    decks_by_id = {deck.id: deck for deck in decks}
    return [
        (deck.id, deck_full_path(deck, decks_by_id))
        for deck in decks
        if not deck.is_archived
    ]


async def list_archived_deck_display_choices(
    session: AsyncSession,
    user: User,
) -> list[tuple[int, str]]:
    decks = await _list_all_user_decks(session, user)
    decks_by_id = {deck.id: deck for deck in decks}
    return [
        (deck.id, deck_full_path(deck, decks_by_id))
        for deck in decks
        if deck.is_archived
    ]


async def _list_all_user_decks(session: AsyncSession, user: User) -> list[Deck]:
    result = await session.execute(
        select(Deck)
        .where(Deck.user_id == user.id)
        .order_by(Deck.name.asc())
    )
    return list(result.scalars())


async def get_deck(session: AsyncSession, user: User, deck_id: int) -> Deck | None:
    result = await session.execute(
        select(Deck).where(
            Deck.id == deck_id,
            Deck.user_id == user.id,
            Deck.is_archived.is_(False),
        )
    )
    return result.scalar_one_or_none()


async def get_any_deck(session: AsyncSession, user: User, deck_id: int) -> Deck | None:
    result = await session.execute(
        select(Deck).where(
            Deck.id == deck_id,
            Deck.user_id == user.id,
        )
    )
    return result.scalar_one_or_none()


async def get_deck_counts(session: AsyncSession, deck: Deck) -> tuple[int, int, int]:
    now = datetime.now(UTC)
    counts = []
    for clause in (
        Card.state == "new",
        Card.state.in_(["learning", "relearning"]),
        Card.state == "review",
    ):
        result = await session.execute(
            select(func.count(Card.id)).where(
                Card.deck_id == deck.id,
                Card.suspended.is_(False),
                Card.due_at <= now,
                clause,
            )
        )
        counts.append(int(result.scalar_one()))
    return counts[0], counts[1], counts[2]


async def deck_list_with_counts(
    session: AsyncSession,
    user: User,
) -> list[tuple[int, str, int, int, int]]:
    all_decks = await _list_all_user_decks(session, user)
    decks = [deck for deck in all_decks if not deck.is_archived]
    rows = []
    decks_by_id = {deck.id: deck for deck in all_decks}
    for deck in decks:
        new_count, learning_count, review_count = await get_deck_counts(session, deck)
        rows.append(
            (
                deck.id,
                deck_full_path(deck, decks_by_id),
                new_count,
                learning_count,
                review_count,
            )
        )
    return rows


async def deck_summary(session: AsyncSession, deck: Deck) -> dict[str, int]:
    queries: dict[str, Select] = {
        "notes": select(func.count(Note.id)).where(Note.deck_id == deck.id),
        "cards": select(func.count(Card.id)).where(Card.deck_id == deck.id),
        "reviews": select(func.count(ReviewLog.id)).where(ReviewLog.deck_id == deck.id),
    }
    summary: dict[str, int] = {}
    for key, query in queries.items():
        result = await session.execute(query)
        summary[key] = int(result.scalar_one())
    new_count, learning_count, review_count = await get_deck_counts(session, deck)
    summary.update(
        {
            "new": new_count,
            "learning": learning_count,
            "review": review_count,
        }
    )
    return summary


async def update_deck_setting(
    session: AsyncSession,
    deck: Deck,
    field: str,
    value: int | float,
) -> None:
    await update_deck_settings(session, deck, {field: value})


async def update_deck_settings(
    session: AsyncSession,
    deck: Deck,
    values: Mapping[str, Any],
) -> None:
    if not values:
        raise ValueError("At least one deck setting is required")
    validated: dict[str, int | float | bool | list[int]] = {}
    for field, value in values.items():
        if field not in DECK_SETTINGS_FIELDS:
            raise ValueError(f"Unsupported deck setting: {field}")
        parsed = validate_deck_setting_value(field, value)
        if parsed is None:
            raise ValueError(f"Invalid deck setting: {field}")
        validated[field] = parsed
    for field, value in validated.items():
        setattr(deck, field, value)
    deck.option_preset = "custom"
    await session.commit()


async def toggle_bury_siblings(session: AsyncSession, deck: Deck) -> None:
    deck.bury_siblings = not deck.bury_siblings
    deck.option_preset = "custom"
    await session.commit()


async def toggle_fuzzing(session: AsyncSession, deck: Deck) -> None:
    deck.enable_fuzzing = not deck.enable_fuzzing
    deck.option_preset = "custom"
    await session.commit()


async def apply_deck_preset(session: AsyncSession, deck: Deck, preset_name: str) -> None:
    preset = DECK_OPTION_PRESETS.get(preset_name)
    if preset is None:
        raise ValueError(f"Unknown deck option preset: {preset_name}")
    for field, value in preset.items():
        setattr(deck, field, value)
    deck.option_preset = preset_name
    await session.commit()


async def rename_deck(session: AsyncSession, deck: Deck, name: str) -> None:
    deck.name = name.strip()
    await session.commit()


async def rename_api_deck(session: AsyncSession, user: User, deck: Deck, name: str) -> None:
    normalized_name = validate_api_deck_name(name)
    parent_clause = Deck.parent_id.is_(None) if deck.parent_id is None else Deck.parent_id == deck.parent_id
    result = await session.execute(
        select(Deck.id).where(
            Deck.user_id == user.id,
            parent_clause,
            Deck.name == normalized_name,
            Deck.id != deck.id,
        )
    )
    if result.scalar_one_or_none() is not None:
        raise DeckNameConflictError("Deck name already exists")
    try:
        await rename_deck(session, deck, normalized_name)
    except IntegrityError as exc:
        await session.rollback()
        raise DeckNameConflictError("Deck name already exists") from exc


async def archive_deck(session: AsyncSession, deck: Deck) -> None:
    deck.is_archived = True
    await session.commit()


async def restore_deck(session: AsyncSession, deck: Deck) -> None:
    deck.is_archived = False
    await session.commit()
