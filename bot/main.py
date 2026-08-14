import asyncio
import logging

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import BotCommand

from bot.config import get_settings
from bot.db import init_db
from bot.handlers import backups, browse, cards, common, decks, errors, import_cards, settings, study
from bot.middlewares.throttling import ThrottlingMiddleware


async def main() -> None:
    settings_obj = get_settings()
    logging.basicConfig(
        level=settings_obj.log_level,
        format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
    )
    if not settings_obj.bot_token or settings_obj.bot_token.startswith("put-"):
        raise RuntimeError("Set BOT_TOKEN in .env before starting the bot.")

    if settings_obj.auto_create_tables:
        await init_db()

    bot = Bot(
        token=settings_obj.bot_token,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    dispatcher = Dispatcher(storage=MemoryStorage())
    dispatcher.message.middleware(ThrottlingMiddleware(rate_limit_seconds=0.35))
    dispatcher.callback_query.middleware(ThrottlingMiddleware(rate_limit_seconds=0.2))
    dispatcher.include_routers(
        common.router,
        decks.router,
        cards.router,
        import_cards.router,
        study.router,
        browse.router,
        backups.router,
        settings.router,
        errors.router,
    )
    await setup_bot_commands(bot)
    await dispatcher.start_polling(bot)


async def setup_bot_commands(bot: Bot) -> None:
    await bot.set_my_commands(
        [
            BotCommand(command="start", description="Запустить бота"),
            BotCommand(command="menu", description="Главное меню"),
            BotCommand(command="cancel", description="Отменить текущее действие"),
            BotCommand(command="status", description="Проверить статус"),
            BotCommand(command="backup", description="Скачать JSON backup"),
            BotCommand(command="restore", description="Восстановить JSON backup"),
            BotCommand(command="help", description="Помощь"),
        ]
    )


if __name__ == "__main__":
    asyncio.run(main())
