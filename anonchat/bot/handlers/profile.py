from __future__ import annotations

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import CallbackQuery, Message

from ...db import session_scope
from ...models import Gender, User
from ...utils import fmt_dt, gender_label, rating_text, vip_status_text
from .. import keyboards as kb
from .. import texts as T

router = Router(name="profile")


def profile_text(u: User) -> str:
    return (
        "👤 <b>Твой профиль</b>\n\n"
        f"Пол: {gender_label(u.gender)}\n"
        f"Возраст: {u.age or '—'}\n"
        f"Рейтинг: {rating_text(u)}\n"
        f"VIP: {vip_status_text(u)}\n\n"
        f"💬 Диалогов: <b>{u.dialogs_count}</b>\n"
        f"✉️ Сообщений: <b>{u.messages_count}</b>\n"
        f"👥 Приглашено друзей: <b>{u.referrals_count}</b>\n"
        f"📅 С нами с {fmt_dt(u.created_at)[:10]}\n\n"
        f"<i>ID: <code>{u.id}</code> — его видишь только ты</i>"
    )


@router.message(Command("profile"))
@router.message(F.text == T.BTN_PROFILE)
async def cmd_profile(message: Message, user: User):
    await message.answer(profile_text(user), reply_markup=kb.profile_kb())


@router.callback_query(F.data == "edit_gender")
async def cb_edit_gender(cb: CallbackQuery):
    await cb.message.answer("Укажи пол:", reply_markup=kb.gender_kb("pg"))
    await cb.answer()


@router.callback_query(F.data.startswith("pg:"))
async def cb_set_gender(cb: CallbackQuery):
    value = cb.data.split(":", 1)[1]
    async with session_scope() as s:
        u = await s.get(User, cb.from_user.id)
        u.gender = None if value == "none" else Gender(value)
    await cb.message.edit_text(f"✅ Пол: {gender_label(u.gender)}")
    await cb.answer()


@router.callback_query(F.data == "edit_age")
async def cb_edit_age(cb: CallbackQuery):
    await cb.message.answer("Укажи возраст:", reply_markup=kb.age_kb("pa"))
    await cb.answer()


@router.callback_query(F.data.startswith("pa:"))
async def cb_set_age(cb: CallbackQuery):
    age = int(cb.data.split(":", 1)[1]) or None
    async with session_scope() as s:
        u = await s.get(User, cb.from_user.id)
        u.age = age
    await cb.message.edit_text(f"✅ Возраст: {age or 'скрыт'}")
    await cb.answer()


@router.message(Command("settings"))
@router.message(F.text == T.BTN_SETTINGS)
async def cmd_settings(message: Message, user: User):
    await message.answer(
        "⚙️ <b>Настройки</b>\n\n<i>Нажимай, чтобы переключить. 🔒 — для VIP.</i>",
        reply_markup=kb.settings_kb(user),
    )


@router.callback_query(F.data.startswith("set:"))
async def cb_setting(cb: CallbackQuery):
    field = cb.data.split(":", 1)[1]
    async with session_scope() as s:
        u = await s.get(User, cb.from_user.id)
        if field in ("protect_content", "pref_gender") and not u.is_vip:
            await cb.answer("🔒 Доступно с VIP — /vip", show_alert=True)
            return
        if field == "pref_gender":
            order = [None, Gender.female, Gender.male]
            u.pref_gender = order[(order.index(u.pref_gender) + 1) % 3]
        elif field in ("show_profile", "allow_media", "protect_content"):
            setattr(u, field, not getattr(u, field))
        else:
            await cb.answer()
            return
    await cb.message.edit_reply_markup(reply_markup=kb.settings_kb(u))
    await cb.answer("Сохранено")
