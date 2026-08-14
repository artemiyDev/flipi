import asyncio

from sqlalchemy import text

from bot.db import async_session


async def main() -> None:
    async with async_session() as session:
        await session.execute(text("select 1"))


if __name__ == "__main__":
    asyncio.run(main())
