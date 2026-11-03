from aiogram.fsm.state import State, StatesGroup


class Onboarding(StatesGroup):
    gender = State()
    age = State()


class EditProfile(StatesGroup):
    age = State()


class CreateRoom(StatesGroup):
    title = State()
    emoji = State()
    description = State()
    privacy = State()
    size = State()
