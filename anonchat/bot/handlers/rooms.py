from __future__ import annotations

from html import escape

from aiogram import Bot, F, Router
from aiogram.filters import Command, CommandObject
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from ...config import get_settings
from ...models import User, UserState
from ...services import matching, rooms
from ...services.messaging import find_message_by_chat_msg
from ...utils import plural
from .. import keyboards as kb
from .. import texts as T
from ..common import broadcast_service, finish_dialog, get_user, leave_current_room, safe_send
from ..states import CreateRoom

router = Router(name="rooms")
PER_PAGE = 8


def _rooms_header(total: int) -> str:
    return (
        "👥 <b>Анонимные комнаты</b>\n\n"
        "Групповые чаты по интересам. В каждой комнате у тебя случайный ник — "
        "никто не знает, кто ты.\n\n"
        f"<i>Комнат: {total}. Цифры — онлайн / мест.</i>"
    )


@router.message(Command("rooms"))
@router.message(F.text == T.BTN_ROOMS)
async def cmd_rooms(message: Message, user: User):
    items, total = await rooms.list_public_rooms(0, PER_PAGE)
    await message.answer(_rooms_header(total), reply_markup=kb.rooms_kb(items, 0, total, PER_PAGE, user))


@router.callback_query(F.data.startswith("rooms:"))
async def cb_rooms_page(cb: CallbackQuery, user: User):
    page = int(cb.data.split(":")[1])
    items, total = await rooms.list_public_rooms(page * PER_PAGE, PER_PAGE)
    await cb.message.edit_text(_rooms_header(total), reply_markup=kb.rooms_kb(items, page, total, PER_PAGE, user))
    await cb.answer()


def _room_card(info: rooms.RoomInfo) -> str:
    r = info.room
    lines = [f"{r.emoji} <b>{escape(r.title)}</b>"]
    if r.description:
        lines.append(f"<i>{escape(r.description)}</i>")
    lines.append("")
    lines.append(f"🟢 Онлайн: <b>{info.online}</b> из {r.max_members}")
    lines.append("🌍 Публичная" if r.is_public else "🔒 Вход по ссылке")
    if r.vip_only:
        lines.append("👑 Только для VIP")
    lines.append(f"💬 Сообщений: {r.messages_count}")
    return "\n".join(lines)


@router.callback_query(F.data.startswith("room:"))
async def cb_room_card(cb: CallbackQuery, user: User):
    info = await rooms.get_room(int(cb.data.split(":")[1]))
    if info is None or not info.room.is_active:
        await cb.answer("Комната закрыта", show_alert=True)
        return
    can_join = not info.room.vip_only or user.is_vip
    text = _room_card(info)
    if not can_join:
        text += "\n\n🔒 Оформи /vip, чтобы войти."
    await cb.message.edit_text(text, reply_markup=kb.room_card_kb(info.room.id, can_join))
    await cb.answer()


async def join_and_greet(bot: Bot, user: User, room_id: int) -> bool:
    u = await get_user(user.id)
    if u.state == UserState.chatting:
        await finish_dialog(bot, u)
    elif u.state == UserState.searching:
        await matching.cancel_search(u.id)
    # если пользователь был в другой комнате — сообщим той комнате
    prev_room = u.current_room_id if u.state == UserState.in_room else None
    if prev_room and prev_room != room_id:
        await leave_current_room(bot, u, notify_me=False)
    try:
        room, member, others = await rooms.join_room(user.id, room_id)
    except rooms.RoomError as e:
        await bot.send_message(user.id, f"⚠️ {e}", reply_markup=kb.menu_for(await get_user(user.id)))
        return False
    online = len(others) + 1
    await bot.send_message(
        user.id,
        f"{room.emoji} <b>{escape(room.title)}</b>\n\n"
        f"Ты в комнате как <b>{escape(member.nickname)}</b>\n"
        f"🟢 Онлайн: {online} {plural(online, 'участник', 'участника', 'участников')}\n\n"
        "Пиши — сообщение увидят все участники.\n"
        "<i>↩️ Отвечай на сообщения, чтобы обратиться к конкретному человеку.</i>",
        reply_markup=kb.room_menu(),
    )
    await broadcast_service(bot, others, f"➕ <b>{escape(member.nickname)}</b> присоединился(-ась)")
    return True


@router.callback_query(F.data.startswith("join:"))
async def cb_join(cb: CallbackQuery, user: User, bot: Bot):
    await cb.answer()
    try:
        await cb.message.edit_reply_markup(reply_markup=None)
    except Exception:
        pass
    await join_and_greet(bot, user, int(cb.data.split(":")[1]))


@router.callback_query(F.data == "room_random")
async def cb_room_random(cb: CallbackQuery, user: User, bot: Bot):
    room_id = await rooms.random_room_id(user)
    if room_id is None:
        await cb.answer("Свободных комнат нет 😔", show_alert=True)
        return
    await cb.answer()
    await join_and_greet(bot, user, room_id)


@router.message(Command("leave"))
@router.message(F.text == T.BTN_LEAVE_ROOM)
async def cmd_leave(message: Message, user: User, bot: Bot):
    u = await get_user(user.id)
    if not await leave_current_room(bot, u):
        await message.answer("Ты не в комнате.", reply_markup=kb.menu_for(u))


@router.message(Command("members"))
@router.message(F.text == T.BTN_MEMBERS)
async def cmd_members(message: Message, user: User):
    u = await get_user(user.id)
    if u.state != UserState.in_room or not u.current_room_id:
        await message.answer("Ты не в комнате.")
        return
    members = await rooms.room_members(u.current_room_id)
    info = await rooms.get_room(u.current_room_id)
    lines = [f"👥 <b>Участники</b> ({len(members)}/{info.room.max_members}):", ""]
    for m in members[:60]:
        me = " ← ты" if m.user_id == u.id else ""
        owner = " 👑" if info.room.owner_id == m.user_id else ""
        lines.append(f"• {escape(m.nickname)}{owner}{me}")
    if len(members) > 60:
        lines.append(f"…и ещё {len(members) - 60}")
    await message.answer("\n".join(lines))


@router.message(F.text == T.BTN_ROOM_INFO)
async def cmd_room_info(message: Message, user: User, bot: Bot):
    u = await get_user(user.id)
    if not u.current_room_id:
        await message.answer("Ты не в комнате.")
        return
    info = await rooms.get_room(u.current_room_id)
    me = await bot.me()
    text = _room_card(info)
    text += f"\n\n🔗 Пригласить: <code>https://t.me/{me.username}?start=room_{info.room.invite_code}</code>"
    await message.answer(text)


@router.message(Command("nick"))
async def cmd_nick(message: Message, command: CommandObject, user: User, bot: Bot):
    u = await get_user(user.id)
    if not u.current_room_id:
        await message.answer("Ник меняется внутри комнаты.")
        return
    if not u.is_vip:
        await message.answer("🔒 Свой ник в комнатах — возможность 👑 VIP. /vip")
        return
    nick = (command.args or "").strip()
    if not 2 <= len(nick) <= 24 or "@" in nick or "http" in nick.lower():
        await message.answer("✏️ Использование: <code>/nick Новый ник</code> (2–24 символа, без ссылок)")
        return
    nick = "👑 " + nick
    try:
        old = await rooms.set_nickname(u.current_room_id, u.id, nick)
    except rooms.RoomError as e:
        await message.answer(f"⚠️ {e}")
        return
    await message.answer(f"✅ Теперь ты <b>{escape(nick)}</b>")
    others = [m.user_id for m in await rooms.room_members(u.current_room_id) if m.user_id != u.id]
    await broadcast_service(bot, others, f"✏️ {escape(old)} теперь <b>{escape(nick)}</b>")


@router.message(Command("kick"))
async def cmd_kick(message: Message, user: User, bot: Bot):
    u = await get_user(user.id)
    if not u.current_room_id or not message.reply_to_message:
        await message.answer("↩️ Ответь командой /kick на сообщение участника (в своей комнате).")
        return
    info = await rooms.get_room(u.current_room_id)
    if info.room.owner_id != u.id and u.id not in get_settings().admin_ids:
        await message.answer("Исключать может только владелец комнаты.")
        return
    row = await find_message_by_chat_msg(u.id, message.reply_to_message.message_id)
    if row is None or row.room_id != u.current_room_id or row.sender_id == u.id:
        await message.answer("Не нашёл участника по этому сообщению.")
        return
    member = await rooms.kick_member(u.current_room_id, row.sender_id)
    if member is None:
        await message.answer("Этот участник уже вышел.")
        return
    await safe_send(bot, row.sender_id, f"⛔ Тебя исключили из комнаты {info.room.emoji} {escape(info.room.title)}",
                    reply_markup=kb.main_menu())
    others = [m.user_id for m in await rooms.room_members(u.current_room_id)]
    await broadcast_service(bot, others, f"⛔ <b>{escape(member.nickname)}</b> исключён(а) владельцем")


# ---------- создание комнаты ----------
@router.callback_query(F.data == "room_create")
async def cb_room_create(cb: CallbackQuery, user: User, state: FSMContext):
    s = get_settings()
    limit = s.vip_rooms_limit if user.is_vip else s.free_rooms_limit
    owned = await rooms.my_rooms(user.id)
    await cb.answer()
    if limit == 0:
        await cb.message.answer(
            "🔒 Создание своих комнат — возможность <b>👑 VIP</b>.\n\n"
            "Публичные или закрытые по ссылке, до 100 человек, право исключать участников. /vip",
            reply_markup=kb.vip_kb(user),
        )
        return
    if len(owned) >= limit:
        await cb.message.answer(f"У тебя уже {len(owned)} комнат (максимум {limit}). Закрой одну из них в «Мои комнаты».")
        return
    await state.set_state(CreateRoom.title)
    await cb.message.answer("➕ <b>Новая комната</b>\n\nКак назовём? (3–40 символов)\n<i>/cancel — отмена</i>")


@router.message(CreateRoom.title, F.text)
async def rc_title(message: Message, state: FSMContext):
    title = message.text.strip()
    if title.startswith("/"):
        await state.clear()
        await message.answer("Отменено.", reply_markup=kb.main_menu())
        return
    if not 3 <= len(title) <= 40:
        await message.answer("Название должно быть от 3 до 40 символов.")
        return
    await state.update_data(title=title)
    await state.set_state(CreateRoom.emoji)
    await message.answer("Выбери иконку:", reply_markup=kb.emoji_kb())


@router.callback_query(CreateRoom.emoji, F.data.startswith("rc_emoji:"))
async def rc_emoji(cb: CallbackQuery, state: FSMContext):
    await state.update_data(emoji=cb.data.split(":", 1)[1])
    await state.set_state(CreateRoom.description)
    await cb.message.edit_text("Коротко опиши комнату (до 200 символов) или отправь /skip")
    await cb.answer()


@router.message(CreateRoom.description, F.text)
async def rc_desc(message: Message, state: FSMContext):
    text = message.text.strip()
    if text == "/cancel":
        await state.clear()
        await message.answer("Отменено.", reply_markup=kb.main_menu())
        return
    await state.update_data(description=None if text == "/skip" else text[:200])
    await state.set_state(CreateRoom.privacy)
    await message.answer("Кто может войти?", reply_markup=kb.room_privacy_kb())


@router.callback_query(CreateRoom.privacy, F.data.startswith("rc_pub:"))
async def rc_privacy(cb: CallbackQuery, state: FSMContext):
    await state.update_data(is_public=cb.data.endswith("1"))
    await state.set_state(CreateRoom.size)
    await cb.message.edit_text("Максимум участников:", reply_markup=kb.room_size_kb())
    await cb.answer()


@router.callback_query(CreateRoom.size, F.data.startswith("rc_size:"))
async def rc_size(cb: CallbackQuery, state: FSMContext, user: User, bot: Bot):
    size = min(int(cb.data.split(":")[1]), get_settings().room_max_size_vip)
    data = await state.get_data()
    await state.clear()
    room = await rooms.create_room(
        user, data["title"], data.get("emoji", "💬"), data.get("description"), data.get("is_public", True), size
    )
    me = await bot.me()
    await cb.message.edit_text(
        f"🎉 Комната {room.emoji} <b>{escape(room.title)}</b> создана!\n\n"
        f"🔗 Ссылка-приглашение:\n<code>https://t.me/{me.username}?start=room_{room.invite_code}</code>\n\n"
        "Как владелец ты можешь исключать участников: ответь на их сообщение командой /kick.",
        reply_markup=kb.my_room_kb(room.id, me.username, room.invite_code),
    )
    await cb.answer()


@router.callback_query(F.data == "my_rooms")
async def cb_my_rooms(cb: CallbackQuery, user: User, bot: Bot):
    owned = await rooms.my_rooms(user.id)
    await cb.answer()
    if not owned:
        await cb.message.answer("У тебя пока нет своих комнат. Нажми «➕ Создать».")
        return
    me = await bot.me()
    for info in owned:
        await cb.message.answer(_room_card(info), reply_markup=kb.my_room_kb(info.room.id, me.username, info.room.invite_code))


@router.callback_query(F.data.startswith("room_close:"))
async def cb_room_close(cb: CallbackQuery, user: User, bot: Bot):
    room_id = int(cb.data.split(":")[1])
    info = await rooms.get_room(room_id)
    if info is None or (info.room.owner_id != user.id and user.id not in get_settings().admin_ids):
        await cb.answer("Нет прав", show_alert=True)
        return
    members = await rooms.close_room(room_id)
    for uid in members:
        await safe_send(bot, uid, f"🗑 Комната {info.room.emoji} {escape(info.room.title)} закрыта владельцем.",
                        reply_markup=kb.main_menu())
    await cb.message.edit_text(f"🗑 Комната <b>{escape(info.room.title)}</b> закрыта.")
    await cb.answer()
