"""Быстрые команды администратора внутри бота."""
from __future__ import annotations

from aiogram import Bot, F, Router
from aiogram.filters import Command, CommandObject
from aiogram.types import Message

from ...config import get_settings
from ...services import stats
from ...services.users import ban_user, grant_vip, unban_user
from .. import keyboards as kb
from ..common import finish_dialog, get_user, leave_current_room, safe_send

router = Router(name="admin")
router.message.filter(F.from_user.id.func(lambda uid: uid in get_settings().admin_ids))


@router.message(Command("admin"))
async def cmd_admin(message: Message):
    d = await stats.overview()
    s = get_settings()
    await message.answer(
        "🛠 <b>Админ</b>\n\n"
        f"👥 Пользователей: <b>{d['users_total']}</b> (+{d['users_today']} сегодня)\n"
        f"🟢 Онлайн: <b>{d['online']}</b> · 🔎 {d['searching']} · 💬 {d['chatting']} · 👥 {d['in_rooms']}\n"
        f"📈 DAU/WAU/MAU: {d['dau']} / {d['wau']} / {d['mau']}\n"
        f"💬 Диалогов сегодня: {d['dialogs_today']} · сообщений: {d['messages_today']}\n"
        f"👑 VIP: {d['vip_now']} · 🚫 бан: {d['banned']} · 🚩 жалоб: {d['reports_open']}\n"
        f"⭐ Звёзд: сегодня {d['stars_today']} · 30д {d['stars_month']} · всего {d['stars_total']}\n\n"
        "<code>/ban id [часы] [причина]</code>\n<code>/unban id</code>\n<code>/givevip id дни</code>",
        reply_markup=kb.admin_kb(s.admin_public_url or None),
    )


@router.message(Command("ban"))
async def cmd_ban(message: Message, command: CommandObject, bot: Bot):
    args = (command.args or "").split(maxsplit=2)
    if not args or not args[0].isdigit():
        await message.answer("Использование: /ban id [часы] [причина]")
        return
    uid = int(args[0])
    hours = int(args[1]) if len(args) > 1 and args[1].isdigit() else None
    reason = args[2] if len(args) > 2 else (args[1] if len(args) > 1 and not args[1].isdigit() else "Нарушение правил")
    await kick_everywhere(bot, uid)
    user = await ban_user(uid, reason, hours)
    if user is None:
        await message.answer("Пользователь не найден")
        return
    await safe_send(bot, uid, f"🚫 Ты заблокирован(а). Причина: {reason}")
    await message.answer(f"✅ {uid} забанен " + (f"на {hours} ч." if hours else "навсегда"))


@router.message(Command("unban"))
async def cmd_unban(message: Message, command: CommandObject):
    if not (command.args or "").strip().isdigit():
        await message.answer("Использование: /unban id")
        return
    await unban_user(int(command.args))
    await message.answer("✅ Разбанен")


@router.message(Command("givevip"))
async def cmd_givevip(message: Message, command: CommandObject, bot: Bot):
    args = (command.args or "").split()
    if len(args) != 2 or not args[0].isdigit() or not args[1].lstrip("-").isdigit():
        await message.answer("Использование: /givevip id дни (-1 = навсегда)")
        return
    user = await grant_vip(int(args[0]), int(args[1]))
    if user is None:
        await message.answer("Пользователь не найден")
        return
    await safe_send(bot, user.id, "🎁 Администратор подарил тебе 👑 VIP!")
    await message.answer("✅ Готово")


async def kick_everywhere(bot: Bot, uid: int) -> None:
    """Выкидывает пользователя из диалога/комнаты/очереди (перед баном)."""
    from ...models import UserState
    from ...services import matching

    u = await get_user(uid)
    if u is None:
        return
    if u.state == UserState.chatting:
        await finish_dialog(bot, u, silent_me=True)
    elif u.state == UserState.in_room:
        await leave_current_room(bot, u, notify_me=False)
    elif u.state == UserState.searching:
        await matching.cancel_search(uid)
