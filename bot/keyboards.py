from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, WebAppInfo
from aiogram.utils.keyboard import InlineKeyboardBuilder


def main_menu() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="Колоды", callback_data="decks:list")
    builder.button(text="Учиться", callback_data="study:choose")
    builder.button(text="Добавить карточку", callback_data="card:add")
    builder.button(text="Импорт", callback_data="import:start")
    builder.button(text="Поиск", callback_data="browse:start")
    builder.button(text="Статистика", callback_data="stats:summary")
    builder.button(text="Настройки", callback_data="settings:menu")
    builder.button(text="Backup JSON", callback_data="backup:json")
    builder.button(text="Restore JSON", callback_data="backup:restore")
    builder.adjust(2, 2, 2, 2, 1)
    return builder.as_markup()


def start_menu(web_app_url: str) -> InlineKeyboardMarkup:
    menu = main_menu()
    if not web_app_url:
        return menu
    return InlineKeyboardMarkup(
        inline_keyboard=[
            *menu.inline_keyboard,
            [InlineKeyboardButton(text="Открыть приложение", web_app=WebAppInfo(url=web_app_url))],
        ]
    )


def share_preview(web_app_url: str, token: str) -> InlineKeyboardMarkup:
    if not web_app_url:
        return main_menu()
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="Открыть в приложении",
                    web_app=WebAppInfo(url=f"{web_app_url}?share={token}"),
                )
            ]
        ]
    )


def back_to_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="В меню", callback_data="menu:main")],
        ]
    )


def deck_actions(deck_id: int) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="Учиться", callback_data=f"study:start:{deck_id}")
    builder.button(text="Повторить заранее", callback_data=f"study:ahead:{deck_id}")
    builder.button(text="Новые без лимита", callback_data=f"study:new:{deck_id}")
    builder.button(text="Добавить карточку", callback_data=f"card:add:{deck_id}")
    builder.button(text="Импорт", callback_data=f"import:deck:{deck_id}")
    builder.button(text="Экспорт CSV", callback_data=f"deck:export:{deck_id}")
    builder.button(text="Настройки", callback_data=f"settings:deck:{deck_id}")
    builder.button(text="Переименовать", callback_data=f"deck:rename:{deck_id}")
    builder.button(text="Архивировать", callback_data=f"deck:archive:{deck_id}")
    builder.button(text="Список колод", callback_data="decks:list")
    builder.button(text="В меню", callback_data="menu:main")
    builder.adjust(2, 2, 2, 2, 2, 1)
    return builder.as_markup()


def deck_list(decks: list[tuple[int, str, int, int, int]]) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for deck_id, name, new_count, learning_count, review_count in decks:
        label = f"{name} | new {new_count} learn {learning_count} due {review_count}"
        builder.button(text=label[:64], callback_data=f"deck:view:{deck_id}")
    builder.button(text="Создать колоду", callback_data="deck:add")
    builder.button(text="Архив", callback_data="decks:archived")
    builder.button(text="В меню", callback_data="menu:main")
    builder.adjust(1)
    return builder.as_markup()


def archived_deck_list(decks: list[tuple[int, str]]) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for deck_id, name in decks:
        builder.button(text=name[:64], callback_data=f"deck:restore:{deck_id}")
    builder.button(text="Список колод", callback_data="decks:list")
    builder.button(text="В меню", callback_data="menu:main")
    builder.adjust(1)
    return builder.as_markup()


def choose_deck(prefix: str, decks: list[tuple[int, str]]) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for deck_id, name in decks:
        builder.button(text=name[:64], callback_data=f"{prefix}:{deck_id}")
    builder.button(text="Создать колоду", callback_data="deck:add")
    builder.button(text="В меню", callback_data="menu:main")
    builder.adjust(1)
    return builder.as_markup()


def choose_import_deck(decks: list[tuple[int, str]]) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="APKG: создать колоды из файла", callback_data="import:apkg_auto")
    for deck_id, name in decks:
        builder.button(text=name[:64], callback_data=f"import:deck:{deck_id}")
    builder.button(text="Создать колоду", callback_data="deck:add")
    builder.button(text="В меню", callback_data="menu:main")
    builder.adjust(1)
    return builder.as_markup()


def choose_study_deck(decks: list[tuple[int, str]]) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="Все колоды", callback_data="study:start:all")
    builder.button(text="Фильтрованная сессия", callback_data="study:filter")
    for deck_id, name in decks:
        builder.button(text=name[:64], callback_data=f"study:start:{deck_id}")
    builder.button(text="В меню", callback_data="menu:main")
    builder.adjust(1)
    return builder.as_markup()


def show_answer(card_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="Показать ответ", callback_data=f"study:show:{card_id}")],
            [InlineKeyboardButton(text="Действия", callback_data=f"card:view:{card_id}")],
            [InlineKeyboardButton(text="Закончить", callback_data="menu:main")],
        ]
    )


def rate_card(card_id: int) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="Снова", callback_data=f"study:rate:{card_id}:1")
    builder.button(text="Трудно", callback_data=f"study:rate:{card_id}:2")
    builder.button(text="Хорошо", callback_data=f"study:rate:{card_id}:3")
    builder.button(text="Легко", callback_data=f"study:rate:{card_id}:4")
    builder.button(text="Действия", callback_data=f"card:view:{card_id}")
    builder.button(text="Закончить", callback_data="menu:main")
    builder.adjust(4, 2)
    return builder.as_markup()


def leech_rescue(
    card_id: int,
    note_id: int,
    review_lapses: int,
) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="Исправить карточку", callback_data=f"note:edit:{note_id}:{card_id}")
    builder.button(
        text="Продолжить учить",
        callback_data=f"leech:resume:{card_id}:{review_lapses}",
    )
    builder.button(
        text="Оставить на потом",
        callback_data=f"leech:later:{card_id}:{review_lapses}",
    )
    builder.adjust(1)
    return builder.as_markup()


def yes_no(yes_data: str, no_data: str) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="Да", callback_data=yes_data)
    builder.button(text="Нет", callback_data=no_data)
    builder.adjust(2)
    return builder.as_markup()


def card_actions(
    card_id: int,
    note_id: int,
    deck_id: int,
    suspended: bool,
    flag: str | None,
) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="Править", callback_data=f"note:edit:{note_id}:{card_id}")
    builder.button(
        text="Возобновить" if suspended else "Приостановить",
        callback_data=f"card:suspend:{card_id}:0" if suspended else f"card:suspend:{card_id}:1",
    )
    builder.button(text="Скрыть до завтра", callback_data=f"card:bury:{card_id}")
    builder.button(text="Назначить дату", callback_data=f"card:due:{card_id}")
    builder.button(text="Сбросить", callback_data=f"card:reset:{card_id}")
    builder.button(text=f"Флаг: {flag}" if flag else "Флаг", callback_data=f"card:flag_menu:{card_id}")
    builder.button(text="Удалить заметку", callback_data=f"note:delete:{note_id}:{card_id}")
    builder.button(text="Учить колоду", callback_data=f"study:start:{deck_id}")
    builder.button(text="В меню", callback_data="menu:main")
    builder.adjust(2, 2, 2, 1, 2)
    return builder.as_markup()


def flag_options(card_id: int, current_flag: str | None) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for color in ("red", "orange", "green", "blue", "purple"):
        label = f"{color} *" if current_flag == color else color
        builder.button(text=label, callback_data=f"card:flag:{card_id}:{color}")
    if current_flag:
        builder.button(text="Убрать флаг", callback_data=f"card:flag:{card_id}:none")
    builder.button(text="К карточке", callback_data=f"card:view:{card_id}")
    builder.adjust(2, 2, 1, 1, 1)
    return builder.as_markup()


def note_edit_fields(note_id: int, card_id: int) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="Вопрос", callback_data=f"note:edit_field:{note_id}:{card_id}:front")
    builder.button(text="Ответ", callback_data=f"note:edit_field:{note_id}:{card_id}:back")
    builder.button(text="Теги", callback_data=f"note:edit_field:{note_id}:{card_id}:tags")
    builder.button(text="К карточке", callback_data=f"card:view:{card_id}")
    builder.adjust(2, 1, 1)
    return builder.as_markup()


def set_due_options(card_id: int) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="Сегодня", callback_data=f"card:due_set:{card_id}:0")
    builder.button(text="Завтра", callback_data=f"card:due_set:{card_id}:1")
    builder.button(text="Через 3 дня", callback_data=f"card:due_set:{card_id}:3")
    builder.button(text="Через 7 дней", callback_data=f"card:due_set:{card_id}:7")
    builder.button(text="Своя дата", callback_data=f"card:due_custom:{card_id}")
    builder.button(text="К карточке", callback_data=f"card:view:{card_id}")
    builder.adjust(2, 2, 1, 1)
    return builder.as_markup()


def browse_results(notes: list[tuple[int, str]]) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for card_id, label in notes:
        builder.button(text=label[:64], callback_data=f"card:view:{card_id}")
    builder.button(text="В меню", callback_data="menu:main")
    builder.adjust(1)
    return builder.as_markup()


def browse_quick_filters() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="К повторению", callback_data="browse:filter:is:due")
    builder.button(text="Новые", callback_data="browse:filter:state:new")
    builder.button(text="Приостановлены", callback_data="browse:filter:is:suspended")
    builder.button(text="Трудные", callback_data="browse:filter:is:leech")
    builder.button(text="С флагом", callback_data="browse:filter:has:flag")
    builder.button(text="В меню", callback_data="menu:main")
    builder.adjust(2, 2, 1, 1)
    return builder.as_markup()


def deck_settings(deck_id: int) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="Presets", callback_data=f"settings:presets:{deck_id}")
    builder.button(text="Новых в день", callback_data=f"settings:edit:{deck_id}:new_cards_per_day")
    builder.button(text="Повторов в день", callback_data=f"settings:edit:{deck_id}:reviews_per_day")
    builder.button(text="Retention", callback_data=f"settings:edit:{deck_id}:desired_retention")
    builder.button(text="Learning steps", callback_data=f"settings:edit:{deck_id}:learning_steps_minutes")
    builder.button(text="Relearning steps", callback_data=f"settings:edit:{deck_id}:relearning_steps_minutes")
    builder.button(text="Max interval", callback_data=f"settings:edit:{deck_id}:maximum_interval_days")
    builder.button(text="Fuzzing", callback_data=f"settings:toggle_fuzz:{deck_id}")
    builder.button(text="Bury siblings", callback_data=f"settings:toggle_bury:{deck_id}")
    builder.button(text="К колоде", callback_data=f"deck:view:{deck_id}")
    builder.button(text="В меню", callback_data="menu:main")
    builder.adjust(1, 2, 2, 2, 2, 2)
    return builder.as_markup()


def deck_preset_options(deck_id: int, current_preset: str) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for preset in ("light", "balanced", "intense", "exam"):
        label = f"{preset} *" if current_preset == preset else preset
        builder.button(text=label, callback_data=f"settings:preset:{deck_id}:{preset}")
    builder.button(text="К настройкам", callback_data=f"settings:deck:{deck_id}")
    builder.adjust(2, 2, 1)
    return builder.as_markup()


def settings_root(timezone: str, has_decks: bool) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text=f"Timezone: {timezone}", callback_data="settings:timezone")
    builder.button(text="Напоминания", callback_data="settings:reminders")
    if has_decks:
        builder.button(text="Настройки колод", callback_data="settings:decks")
    builder.button(text="В меню", callback_data="menu:main")
    builder.adjust(1)
    return builder.as_markup()


def reminder_settings(enabled: bool) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(
        text="Выключить" if enabled else "Включить",
        callback_data="reminder:toggle",
    )
    builder.button(text="Изменить время", callback_data="reminder:time")
    builder.button(text="К настройкам", callback_data="settings:menu")
    builder.adjust(1)
    return builder.as_markup()


def reminder_actions(web_app_url: str) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    if web_app_url:
        builder.button(text="Учить сейчас", web_app=WebAppInfo(url=web_app_url))
    else:
        builder.button(text="Учить сейчас", callback_data="menu:main")
    builder.button(text="Через 2 часа", callback_data="reminder:snooze")
    builder.button(text="Сегодня не надо", callback_data="reminder:skip")
    builder.adjust(1, 2)
    return builder.as_markup()
