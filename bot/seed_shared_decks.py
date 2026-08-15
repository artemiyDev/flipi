import asyncio
import json
import sys
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from bot.db import async_session
from bot.models import SharedDeck

REQUIRED_FIELDS = frozenset({"slug", "title", "description", "language", "tags", "notes"})
DEFAULT_SEED_DIR = Path(__file__).resolve().parents[1] / "seed"


@dataclass(frozen=True)
class SeedResult:
    filename: str
    status: str
    error: str | None = None


def load_seed_deck(path: Path) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"invalid JSON: {exc}") from exc

    if not isinstance(data, dict):
        raise ValueError("root must be an object")
    missing = REQUIRED_FIELDS - data.keys()
    if missing:
        raise ValueError(f"missing required fields: {', '.join(sorted(missing))}")
    if not isinstance(data["slug"], str) or not data["slug"] or len(data["slug"]) > 64:
        raise ValueError("slug must be a non-empty string up to 64 characters")
    if not isinstance(data["title"], str) or not data["title"] or len(data["title"]) > 255:
        raise ValueError("title must be a non-empty string up to 255 characters")
    if not isinstance(data["description"], str):
        raise ValueError("description must be a string")
    if not isinstance(data["language"], str) or len(data["language"]) > 16:
        raise ValueError("language must be a string up to 16 characters")
    if not _is_string_list(data["tags"]):
        raise ValueError("tags must be an array of strings")
    if not isinstance(data["notes"], list):
        raise ValueError("notes must be an array")

    for index, note in enumerate(data["notes"]):
        if not isinstance(note, dict):
            raise ValueError(f"notes[{index}] must be an object")
        if not isinstance(note.get("front"), str) or not note["front"].strip():
            raise ValueError(f"notes[{index}].front must be a non-empty string")
        if not isinstance(note.get("back"), str) or not note["back"].strip():
            raise ValueError(f"notes[{index}].back must be a non-empty string")
        if not isinstance(note.get("reverse"), bool):
            raise ValueError(f"notes[{index}].reverse must be a boolean")
        if "tags" in note and not _is_string_list(note["tags"]):
            raise ValueError(f"notes[{index}].tags must be an array of strings")
    return data


async def seed_shared_decks(
    seed_dir: Path = DEFAULT_SEED_DIR,
    session_factory: async_sessionmaker[AsyncSession] = async_session,
) -> list[SeedResult]:
    results: list[SeedResult] = []
    for path in sorted(seed_dir.glob("*.json")):
        try:
            payload = load_seed_deck(path)
            async with session_factory() as session:
                existing = await session.scalar(
                    select(SharedDeck).where(SharedDeck.slug == payload["slug"])
                )
                if existing is None:
                    session.add(
                        SharedDeck(
                            slug=payload["slug"],
                            title=payload["title"],
                            description=payload["description"],
                            language=payload["language"],
                            tags=payload["tags"],
                            notes=payload["notes"],
                            notes_count=len(payload["notes"]),
                        )
                    )
                    status = "created"
                else:
                    changes = {
                        "title": payload["title"],
                        "description": payload["description"],
                        "language": payload["language"],
                        "tags": payload["tags"],
                        "notes": payload["notes"],
                        "notes_count": len(payload["notes"]),
                    }
                    status = "unchanged"
                    for field, value in changes.items():
                        if getattr(existing, field) != value:
                            setattr(existing, field, value)
                            status = "updated"
                await session.commit()
            result = SeedResult(path.name, status)
            print(f"{result.filename}: {result.status}")
        except ValueError as exc:
            result = SeedResult(path.name, "error", str(exc))
            print(f"{result.filename}: error: {result.error}", file=sys.stderr)
        results.append(result)
    return results


def _is_string_list(value: object) -> bool:
    return isinstance(value, list) and all(isinstance(item, str) for item in value)


def main() -> None:
    results = asyncio.run(seed_shared_decks())
    if any(result.status == "error" for result in results):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
