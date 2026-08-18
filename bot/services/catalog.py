from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from bot.models import Deck, SharedDeck, User
from bot.services.cards import create_basic_note
from bot.services.decks import available_root_deck_name
from bot.services.events import track


class CatalogDeckAlreadyInstalledError(ValueError):
    """Raised when a catalog deck is already active for a user."""


@dataclass(frozen=True)
class CatalogInstallResult:
    deck_id: int
    added: int


async def list_catalog_decks(session: AsyncSession, user: User) -> list[dict]:
    installed_result = await session.execute(
        select(Deck.source_slug).where(
            Deck.user_id == user.id,
            Deck.is_archived.is_(False),
            Deck.source_slug.is_not(None),
        )
    )
    installed_slugs = set(installed_result.scalars())
    result = await session.execute(select(SharedDeck).order_by(SharedDeck.title.asc()))
    return [
        {
            "slug": deck.slug,
            "title": deck.title,
            "description": deck.description,
            "language": deck.language,
            "tags": deck.tags,
            "notes_count": deck.notes_count,
            "installed": deck.slug in installed_slugs,
        }
        for deck in result.scalars()
    ]


async def install_catalog_deck(
    session: AsyncSession,
    user: User,
    slug: str,
) -> CatalogInstallResult | None:
    shared_deck = await session.scalar(select(SharedDeck).where(SharedDeck.slug == slug))
    if shared_deck is None:
        return None

    active_install = await session.scalar(
        select(Deck.id).where(
            Deck.user_id == user.id,
            Deck.is_archived.is_(False),
            Deck.source_slug == slug,
        )
    )
    if active_install is not None:
        raise CatalogDeckAlreadyInstalledError("Catalog deck is already installed")

    deck_name = await available_root_deck_name(session, user, shared_deck.title, "catalog")
    deck = Deck(
        user_id=user.id,
        name=deck_name,
        description=shared_deck.description,
        source_slug=shared_deck.slug,
    )
    session.add(deck)
    await session.flush()

    for item in shared_deck.notes:
        note_tags = _merge_tags(shared_deck.tags, item.get("tags", []))
        await create_basic_note(
            session,
            user,
            deck,
            item["front"],
            item["back"],
            tags=note_tags,
            create_reverse=item["reverse"],
            commit=False,
        )

    await track(session, user.id, "catalog_install", slug=slug, added=len(shared_deck.notes))
    await session.commit()
    await session.refresh(deck)
    return CatalogInstallResult(deck_id=deck.id, added=len(shared_deck.notes))


def _merge_tags(deck_tags: list[str], note_tags: list[str]) -> list[str]:
    return list(dict.fromkeys([*deck_tags, *note_tags]))
