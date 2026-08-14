from aiogram.fsm.state import State, StatesGroup


class AddDeck(StatesGroup):
    name = State()
    description = State()


class AddCard(StatesGroup):
    deck_id = State()
    front = State()
    back = State()
    tags = State()
    reverse = State()


class ImportCards(StatesGroup):
    deck_id = State()
    payload = State()


class BrowseCards(StatesGroup):
    query = State()


class EditNote(StatesGroup):
    value = State()


class SetDueDate(StatesGroup):
    value = State()


class EditDeckSetting(StatesGroup):
    value = State()


class EditUserTimezone(StatesGroup):
    value = State()


class EditDeck(StatesGroup):
    name = State()


class FilteredStudy(StatesGroup):
    query = State()


class RestoreBackup(StatesGroup):
    payload = State()
