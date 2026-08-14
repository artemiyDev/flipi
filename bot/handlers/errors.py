import logging

from aiogram import Router
from aiogram.types import ErrorEvent

router = Router()
logger = logging.getLogger(__name__)


@router.errors()
async def handle_error(event: ErrorEvent) -> bool:
    logger.exception("Unhandled bot update", exc_info=event.exception)

    update = event.update
    if update.callback_query:
        await update.callback_query.answer("Произошла ошибка. Попробуйте ещё раз.", show_alert=False)
        if update.callback_query.message:
            await update.callback_query.message.answer("Произошла ошибка. Можно вернуться в /menu или выполнить /cancel.")
    elif update.message:
        await update.message.answer("Произошла ошибка. Можно вернуться в /menu или выполнить /cancel.")

    return True
