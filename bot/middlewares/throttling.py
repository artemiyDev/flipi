from collections.abc import Awaitable, Callable
from time import monotonic
from typing import Any

from aiogram import BaseMiddleware
from aiogram.types import CallbackQuery, TelegramObject


class ThrottlingMiddleware(BaseMiddleware):
    def __init__(self, rate_limit_seconds: float = 0.35) -> None:
        self.rate_limit_seconds = rate_limit_seconds
        self._last_seen: dict[tuple[int, str], float] = {}

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        user = data.get("event_from_user") or getattr(event, "from_user", None)
        if user is None:
            return await handler(event, data)

        key = (user.id, event.__class__.__name__)
        now = monotonic()
        previous = self._last_seen.get(key, 0)
        if now - previous < self.rate_limit_seconds:
            if isinstance(event, CallbackQuery):
                await event.answer("Слишком быстро.", show_alert=False)
            return None

        self._last_seen[key] = now
        return await handler(event, data)
