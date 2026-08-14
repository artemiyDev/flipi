from datetime import UTC, datetime
from io import BytesIO

from aiogram import Bot, F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import BufferedInputFile, CallbackQuery, Message

from bot.db import async_session
from bot.services.backups import restore_user_backup_json
from bot.services.exporters import export_user_backup_json
from bot.services.users import get_or_create_user
from bot.states import RestoreBackup

router = Router()
MAX_BACKUP_BYTES = 20 * 1024 * 1024


@router.callback_query(F.data == "backup:json")
async def backup_callback(callback: CallbackQuery) -> None:
    await callback.answer()
    if callback.from_user is None or callback.message is None:
        return
    await _send_backup(callback.message, callback.from_user)


@router.message(Command("backup"))
async def backup_command(message: Message) -> None:
    if message.from_user is None:
        return
    await _send_backup(message, message.from_user)


@router.callback_query(F.data == "backup:restore")
async def restore_callback(callback: CallbackQuery, state: FSMContext) -> None:
    await callback.answer()
    if callback.message is None:
        return
    await state.set_state(RestoreBackup.payload)
    await callback.message.answer("Отправьте JSON backup-файл.")


@router.message(Command("restore"))
async def restore_command(message: Message, state: FSMContext) -> None:
    await state.set_state(RestoreBackup.payload)
    await message.answer("Отправьте JSON backup-файл.")


@router.message(RestoreBackup.payload)
async def restore_payload(message: Message, state: FSMContext, bot: Bot) -> None:
    if message.from_user is None:
        return
    if message.document is None:
        await message.answer("Нужен JSON-файл из `/backup`.")
        return
    if message.document.file_size and message.document.file_size > MAX_BACKUP_BYTES:
        await message.answer("Файл слишком большой. Текущий лимит restore: 20 MB.")
        return
    if not (message.document.file_name or "").lower().endswith(".json"):
        await message.answer("Нужен файл с расширением .json.")
        return

    buffer = BytesIO()
    await bot.download(message.document, destination=buffer)

    try:
        async with async_session() as session:
            user = await get_or_create_user(session, message.from_user)
            stats = await restore_user_backup_json(session, user, buffer.getvalue())
    except Exception as exc:
        await message.answer(f"Не удалось восстановить backup: {exc}")
        return

    await state.clear()
    await message.answer(
        "Backup восстановлен.\n"
        f"Колод обработано: {stats['decks']}\n"
        f"Заметок добавлено: {stats['notes']}\n"
        f"Карточек добавлено: {stats['cards']}\n"
        f"Review logs добавлено: {stats['reviews']}\n"
        f"Media добавлено: {stats['media']}\n"
        f"Media пропущено: {stats['skipped_media']}\n"
        f"Заметок пропущено как дубли: {stats['skipped_notes']}"
    )


async def _send_backup(message: Message, tg_user) -> None:
    async with async_session() as session:
        user = await get_or_create_user(session, tg_user)
        payload = await export_user_backup_json(session, user)

    timestamp = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
    await message.answer_document(
        BufferedInputFile(payload, filename=f"ankibot_backup_{timestamp}.json"),
        caption="Backup всех колод, карточек и review logs.",
    )
