import mimetypes
import re

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from bot.models import Deck, MediaFile, User
from bot.services.apkg_importer import ImportedMedia


async def save_imported_media_files(
    session: AsyncSession,
    user: User,
    deck: Deck | None,
    media_files: list[ImportedMedia],
) -> tuple[int, int]:
    saved = 0
    skipped = 0
    for media in media_files:
        exists = await _media_exists(session, user, media.original_name, media.sha256)
        if exists:
            skipped += 1
            continue
        session.add(
            MediaFile(
                user_id=user.id,
                deck_id=deck.id if deck else None,
                original_name=media.original_name,
                content_type=mimetypes.guess_type(media.original_name)[0],
                size_bytes=len(media.content),
                sha256=media.sha256,
                content=media.content,
            )
        )
        saved += 1
    await session.flush()
    return saved, skipped


def extract_media_references(*texts: str | None) -> list[str]:
    names: list[str] = []
    seen: set[str] = set()
    for text in texts:
        if not text:
            continue
        for name in re.findall(r"\[(?:sound|media):([^\]]+)\]", text):
            if name not in seen:
                names.append(name)
                seen.add(name)
    return names


def strip_media_references(text: str) -> str:
    return re.sub(r"\[(?:sound|media):[^\]]+\]", "", text).strip()


def replace_image_media_references(text: str, media_files: list[MediaFile]) -> str:
    media_by_name = {media.original_name: media for media in media_files}

    def replace(match: re.Match) -> str:
        media = media_by_name.get(match.group(1))
        if media is not None and (media.content_type or "").startswith("image/"):
            return f'<img src="/api/media/{media.id}">'
        return ""

    return re.sub(r"\[(?:sound|media):([^\]]+)\]", replace, text)


async def get_media_files_by_names(
    session: AsyncSession,
    user: User,
    names: list[str],
) -> list[MediaFile]:
    if not names:
        return []
    result = await session.execute(
        select(MediaFile)
        .where(MediaFile.user_id == user.id, MediaFile.original_name.in_(names))
        .order_by(MediaFile.id.asc())
    )
    media_by_name: dict[str, MediaFile] = {}
    for media in result.scalars():
        media_by_name.setdefault(media.original_name, media)
    return [media_by_name[name] for name in names if name in media_by_name]


async def get_media_file(session: AsyncSession, user: User, media_id: int) -> MediaFile | None:
    result = await session.execute(
        select(MediaFile).where(MediaFile.id == media_id, MediaFile.user_id == user.id)
    )
    return result.scalar_one_or_none()


async def _media_exists(
    session: AsyncSession,
    user: User,
    original_name: str,
    sha256: str,
) -> bool:
    result = await session.execute(
        select(MediaFile.id)
        .where(
            MediaFile.user_id == user.id,
            MediaFile.original_name == original_name,
            MediaFile.sha256 == sha256,
        )
        .limit(1)
    )
    return result.scalar_one_or_none() is not None
