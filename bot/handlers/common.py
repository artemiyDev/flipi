from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.filters import Command, CommandStart
from aiogram.types import CallbackQuery, Message
from sqlalchemy import text

from bot.config import get_settings
from bot.db import async_session
from bot.keyboards import back_to_menu, main_menu, share_preview, start_menu
from bot.services.events import track
from bot.services.shares import share_preview as get_share_preview
from bot.services.users import get_or_create_user

router = Router()


@router.message(CommandStart())
async def start(message: Message, state: FSMContext) -> None:
    await state.clear()
    if message.from_user is None:
        return
    async with async_session() as session:
        user = await get_or_create_user(session, message.from_user)
        await track(session, user.id, "bot_start", source="start")
        token = _share_token(message.text)
        preview = await get_share_preview(session, user, token) if token else None
        if token and preview is not None:
            await track(session, user.id, "share_opened", token=token, via="bot")
        await session.commit()
    if token and preview is not None:
        await message.answer(
            f"{preview['title']} — {preview['cards_count']} карточек · от {preview['author']}",
            reply_markup=share_preview(get_settings().web_app_url, token),
        )
        return
    stale_link = "\n\nСсылка на колоду устарела." if token else ""
    await message.answer(
        "Это бот для интервального повторения. Выберите действие в меню." + stale_link,
        reply_markup=start_menu(get_settings().web_app_url),
    )


def _share_token(text: str | None) -> str | None:
    if not text:
        return None
    parts = text.split(maxsplit=1)
    if len(parts) != 2 or not parts[1].startswith("deck_"):
        return None
    token = parts[1].removeprefix("deck_").strip()
    return token or None


@router.message(Command("menu"))
async def menu_command(message: Message, state: FSMContext) -> None:
    await state.clear()
    await message.answer("Главное меню.", reply_markup=main_menu())


@router.message(Command("cancel"))
async def cancel_command(message: Message, state: FSMContext) -> None:
    await state.clear()
    await message.answer("Текущее действие отменено.", reply_markup=main_menu())


@router.message(Command("status"))
async def status_command(message: Message) -> None:
    try:
        async with async_session() as session:
            await session.execute(text("select 1"))
            table_result = await session.execute(text("select to_regclass('public.alembic_version')"))
            if table_result.scalar_one_or_none():
                version_result = await session.execute(
                    text("select version_num from alembic_version limit 1")
                )
                schema_version = version_result.scalar_one_or_none()
            else:
                schema_version = None
    except Exception as exc:
        await message.answer(f"Бот запущен, но база данных недоступна: {exc}")
        return
    await message.answer(
        f"Бот запущен, база данных отвечает.\nSchema version: {schema_version or 'unknown'}",
        reply_markup=main_menu(),
    )


@router.callback_query(F.data == "menu:main")
async def menu_callback(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await callback.answer()
    if callback.message:
        await callback.message.answer("Главное меню.", reply_markup=main_menu())


@router.message(Command("help"))
async def help_command(message: Message) -> None:
    await message.answer(
        "Формат импорта: две или три колонки. Первая колонка - вопрос, вторая - ответ, третья - теги. "
        "Четвёртая колонка может быть `reverse`, чтобы создать обратную карточку. "
        "Можно отправить CSV, TSV, TXT или просто вставить строки сообщением.",
        reply_markup=back_to_menu(),
    )
