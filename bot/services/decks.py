from datetime import UTC, datetime

from collections.abc import Mapping

from sqlalchemy import Select, func, select
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


async def get_or_create_deck(
    session: AsyncSession,
    user: User,
    name: str,
    description: str | None = None,
    parent: Deck | None = None,
) -> Deck:
    result = await session.execute(
        select(Deck).where(Deck.user_id == user.id, Deck.name == name.strip())
    )
    deck = result.scalar_one_or_none()
    if deck is not None:
        if deck.is_archived:
            deck.is_archived = False
            await session.commit()
        return deck
    return await create_deck(session, user, name, description, parent)


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
    for segment in name.split("::"):
        parent = await get_or_create_deck(session, user, segment, description, parent)
    return parent


async def _get_deck_by_name(
    session: AsyncSession,
    user: User,
    name: str,
) -> Deck | None:
    result = await session.execute(
        select(Deck).where(Deck.user_id == user.id, Deck.name == name)
    )
    return result.scalar_one_or_none()


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
    if field not in {
        "new_cards_per_day",
        "reviews_per_day",
        "desired_retention",
        "learning_steps_minutes",
        "relearning_steps_minutes",
        "maximum_interval_days",
    }:
        raise ValueError(f"Unsupported deck setting: {field}")
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


async def archive_deck(session: AsyncSession, deck: Deck) -> None:
    deck.is_archived = True
    await session.commit()


async def restore_deck(session: AsyncSession, deck: Deck) -> None:
    deck.is_archived = False
    await session.commit()
