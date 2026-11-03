from __future__ import annotations

from aiogram import Bot, F, Router
from aiogram.filters import Command, CommandObject, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from ...config import get_settings
from ...db import session_scope
from ...models import Gender, User
from ...services import stats
from .. import keyboards as kb
from .. import texts as T
from ..states import Onboarding

router = Router(name="start")


async def show_menu(message: Message, user: User) -> None:
    data = await stats.overview()
    await message.answer(T.MENU.format(online=max(data["online"], 1)), reply_markup=kb.menu_for(user))


@router.message(CommandStart())
async def cmd_start(message: Message, command: CommandObject, user: User, state: FSMContext, bot: Bot):
    await state.clear()
    payload = command.args or ""

    if payload.startswith("room_") and user.onboarded:
        from .rooms import join_and_greet
        from ...services.rooms import get_room_by_code

        room = await get_room_by_code(payload[5:])
        if room:
            await join_and_greet(bot, user, room.id)
            return
        await message.answer("😕 Ссылка на комнату недействительна.")

    if not user.onboarded:
        if payload.startswith("room_"):
            await state.update_data(pending_room=payload[5:])
        await message.answer(T.WELCOME)
        await message.answer(T.ASK_GENDER, reply_markup=kb.gender_kb())
        await state.set_state(Onboarding.gender)
        return

    await message.answer(f"👋 С возвращением, <b>{message.from_user.first_name or 'друг'}</b>!")
    await show_menu(message, user)


@router.callback_query(F.data.startswith("ob_gender:"))
async def ob_gender(cb: CallbackQuery, state: FSMContext):
    value = cb.data.split(":", 1)[1]
    async with session_scope() as s:
        u = await s.get(User, cb.from_user.id)
        u.gender = Gender(value) if value != "none" else None
    await cb.message.edit_text(T.ASK_AGE, reply_markup=kb.age_kb())
    await state.set_state(Onboarding.age)
    await cb.answer()


async def _finish_onboarding(message: Message, user_id: int, age: int | None, state: FSMContext, bot: Bot):
    async with session_scope() as s:
        u = await s.get(User, user_id)
        u.age = age
        u.onboarded = True
    data = await state.get_data()
    await state.clear()
    await message.answer(T.ONBOARD_DONE.format(search=T.BTN_SEARCH), reply_markup=kb.main_menu())
    if code := data.get("pending_room"):
        from ...services.rooms import get_room_by_code
        from .rooms import join_and_greet

        room = await get_room_by_code(code)
        if room:
            async with session_scope() as s:
                u = await s.get(User, user_id)
            await join_and_greet(bot, u, room.id)


@router.callback_query(F.data.startswith("ob_age:"))
async def ob_age(cb: CallbackQuery, state: FSMContext, bot: Bot):
    age = int(cb.data.split(":", 1)[1]) or None
    await cb.message.edit_reply_markup(reply_markup=None)
    await cb.answer()
    await _finish_onboarding(cb.message, cb.from_user.id, age, state, bot)


@router.message(Onboarding.age, F.text.regexp(r"^\d{1,2}$"))
async def ob_age_text(message: Message, state: FSMContext, bot: Bot):
    age = int(message.text)
    if not 14 <= age <= 99:
        await message.answer("🤔 Укажи реальный возраст (от 14 до 99).")
        return
    if age < 16:
        await message.answer("😔 Бот доступен с 16 лет. Возвращайся позже!")
        await state.clear()
        return
    await _finish_onboarding(message, message.from_user.id, age, state, bot)


@router.message(Onboarding.gender)
@router.message(Onboarding.age)
async def ob_wrong(message: Message):
    await message.answer("👆 Выбери вариант кнопкой выше (или отправь возраст числом).")


@router.message(Command("menu"))
async def cmd_menu(message: Message, user: User):
    await show_menu(message, user)


@router.message(Command("help"))
@router.message(F.text == T.BTN_HELP)
async def cmd_help(message: Message):
    await message.answer(T.HELP)


@router.message(Command("rules", "terms"))
async def cmd_rules(message: Message):
    await message.answer(T.RULES)


@router.message(Command("paysupport"))
async def cmd_paysupport(message: Message):
    s = get_settings()
    support = f"@{s.support_username}" if s.support_username else "администратору бота"
    await message.answer(T.PAYSUPPORT.format(support=support))


@router.callback_query(F.data == "close")
async def cb_close(cb: CallbackQuery):
    try:
        await cb.message.delete()
    except Exception:
        await cb.message.edit_reply_markup(reply_markup=None)
    await cb.answer()


@router.callback_query(F.data == "noop")
async def cb_noop(cb: CallbackQuery):
    await cb.answer()
