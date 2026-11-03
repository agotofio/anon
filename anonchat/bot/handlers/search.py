from __future__ import annotations

from aiogram import Bot, F, Router
from aiogram.filters import Command
from aiogram.types import CallbackQuery, Message
from sqlalchemy import select

from ...config import get_settings
from ...db import session_scope
from ...models import Dialog, Gender, Message as Msg, Rating, Report, User, UserState
from ...services import matching
from .. import keyboards as kb
from .. import texts as T
from ..common import finish_dialog, get_user, leave_current_room, notify_match, safe_send

router = Router(name="search")

VIP_ONLY = "🔒 Это возможность <b>👑 VIP</b>.\n\nПоиск по полу и возрасту, приоритет в очереди и многое другое — /vip"


def _filters_text(user: User) -> str:
    if not user.is_vip:
        return ""
    parts = []
    if user.pref_gender == Gender.female:
        parts.append("👩 девушки")
    elif user.pref_gender == Gender.male:
        parts.append("👨 парни")
    if user.pref_age_min or user.pref_age_max:
        parts.append(f"🎂 {user.pref_age_min or '…'}–{user.pref_age_max or '…'} лет")
    parts.append("⚡ приоритет")
    return "👑 Фильтры: " + ", ".join(parts) + "\n\n"


async def do_search(bot: Bot, user_id: int) -> None:
    user = await get_user(user_id)
    if user.state == UserState.chatting:
        await bot.send_message(user_id, T.ALREADY_CHATTING.format(stop=T.BTN_STOP), reply_markup=kb.chat_menu())
        return
    if user.state == UserState.searching:
        await bot.send_message(user_id, T.ALREADY_SEARCHING, reply_markup=kb.searching_menu())
        return
    if user.state == UserState.in_room:
        await leave_current_room(bot, user, notify_me=False)

    match = await matching.start_search(user_id)
    if match:
        await notify_match(bot, match)
        return
    queue = await matching.queue_size()
    await bot.send_message(
        user_id,
        T.SEARCHING.format(filters=_filters_text(user), queue=max(queue - 1, 0)),
        reply_markup=kb.searching_menu(),
    )


@router.message(Command("search", "find"))
@router.message(F.text == T.BTN_SEARCH)
async def cmd_search(message: Message, bot: Bot):
    await do_search(bot, message.from_user.id)


@router.callback_query(F.data == "search")
async def cb_search(cb: CallbackQuery, bot: Bot):
    await cb.answer()
    await cb.message.edit_reply_markup(reply_markup=None)
    await do_search(bot, cb.from_user.id)


# ---------- поиск по полу / возрасту ----------
@router.message(F.text == T.BTN_SEARCH_GENDER)
async def gender_search_menu(message: Message, user: User):
    text = "🎯 <b>Кого будем искать?</b>"
    if not user.is_vip:
        text += "\n\n<i>Выбор пола и возраста доступен 👑 VIP. «Кого угодно» — бесплатно.</i>"
    await message.answer(text, reply_markup=kb.gender_search_kb(user))


@router.callback_query(F.data.startswith("gsearch:"))
async def cb_gender_search(cb: CallbackQuery, user: User, bot: Bot):
    value = cb.data.split(":", 1)[1]
    if value != "any" and not user.is_vip:
        await cb.answer()
        await cb.message.answer(VIP_ONLY, reply_markup=kb.vip_kb(user))
        return
    async with session_scope() as s:
        u = await s.get(User, user.id)
        u.pref_gender = None if value == "any" else Gender(value)
    await cb.answer()
    await cb.message.edit_reply_markup(reply_markup=None)
    await do_search(bot, user.id)


@router.callback_query(F.data == "agefilter")
async def cb_age_filter(cb: CallbackQuery, user: User):
    await cb.answer()
    if not user.is_vip:
        await cb.message.answer(VIP_ONLY, reply_markup=kb.vip_kb(user))
        return
    await cb.message.answer("🎂 <b>Возраст собеседника</b>", reply_markup=kb.age_filter_kb())


@router.callback_query(F.data.startswith("agef:"))
async def cb_age_set(cb: CallbackQuery, user: User):
    if not user.is_vip:
        await cb.answer("Только для VIP", show_alert=True)
        return
    _, lo, hi = cb.data.split(":")
    async with session_scope() as s:
        u = await s.get(User, user.id)
        u.pref_age_min = int(lo) or None
        u.pref_age_max = (int(hi) if int(hi) != 99 else None) or None
    label = "любой" if lo == "0" else (f"{lo}+" if hi == "99" else f"{lo}–{hi}")
    await cb.message.edit_text(f"✅ Возраст собеседника: <b>{label}</b>")
    await cb.answer("Сохранено")


# ---------- отмена / стоп / следующий ----------
@router.message(Command("cancel"))
@router.message(F.text == T.BTN_CANCEL_SEARCH)
async def cmd_cancel(message: Message, user: User):
    if await matching.cancel_search(user.id):
        await message.answer(T.SEARCH_CANCELLED, reply_markup=kb.main_menu())
    else:
        u = await get_user(user.id)
        await message.answer("Поиск не идёт.", reply_markup=kb.menu_for(u))


@router.message(Command("stop"))
@router.message(F.text == T.BTN_STOP)
async def cmd_stop(message: Message, user: User, bot: Bot):
    u = await get_user(user.id)
    if u.state == UserState.chatting:
        await finish_dialog(bot, u)
        await message.answer("🏠 Главное меню", reply_markup=kb.main_menu())
        if u.is_vip:
            await message.answer("Передумал(а)? 👑", reply_markup=kb.reconnect_kb())
    elif u.state == UserState.searching:
        await matching.cancel_search(u.id)
        await message.answer(T.SEARCH_CANCELLED, reply_markup=kb.main_menu())
    elif u.state == UserState.in_room:
        await leave_current_room(bot, u)
    else:
        await message.answer(T.NO_DIALOG.format(search=T.BTN_SEARCH), reply_markup=kb.main_menu())


@router.message(Command("next"))
@router.message(F.text == T.BTN_NEXT)
async def cmd_next(message: Message, user: User, bot: Bot):
    u = await get_user(user.id)
    if u.state == UserState.chatting:
        await finish_dialog(bot, u)
    await do_search(bot, user.id)


# ---------- оценки ----------
@router.callback_query(F.data.startswith("rate:"))
async def cb_rate(cb: CallbackQuery, user: User):
    _, dialog_id, value = cb.data.split(":")
    dialog_id, value = int(dialog_id), int(value)
    async with session_scope() as s:
        dialog = await s.get(Dialog, dialog_id)
        if dialog is None or user.id not in (dialog.user1_id, dialog.user2_id):
            await cb.answer("Диалог не найден")
            return
        target_id = dialog.partner_of(user.id)
        exists = (
            await s.execute(select(Rating.id).where(Rating.dialog_id == dialog_id, Rating.from_user_id == user.id))
        ).first()
        if exists:
            await cb.answer("Ты уже оценил(а) этот диалог")
            return
        s.add(Rating(dialog_id=dialog_id, from_user_id=user.id, to_user_id=target_id, value=value))
        target = await s.get(User, target_id)
        if value > 0:
            target.likes += 1
        else:
            target.dislikes += 1
    try:
        await cb.message.edit_reply_markup(reply_markup=kb.after_rate_kb(dialog_id))
    except Exception:
        pass
    await cb.answer(T.RATE_THANKS_LIKE if value > 0 else T.RATE_THANKS_DISLIKE, show_alert=value < 0)


# ---------- жалобы ----------
@router.message(Command("report"))
@router.message(F.text == T.BTN_REPORT)
async def cmd_report(message: Message, user: User):
    u = await get_user(user.id)
    if u.state == UserState.chatting and u.current_dialog_id:
        await message.answer(T.REPORT_CHOOSE, reply_markup=kb.report_reasons_kb(f"d:{u.current_dialog_id}"))
        return
    if u.state == UserState.in_room:
        from ...services.messaging import find_message_by_chat_msg

        if not message.reply_to_message:
            await message.answer("↩️ Ответь командой /report на сообщение нарушителя.")
            return
        row = await find_message_by_chat_msg(user.id, message.reply_to_message.message_id)
        if row is None or row.sender_id == user.id:
            await message.answer("Не нашёл это сообщение 🤷")
            return
        await message.answer(T.REPORT_CHOOSE, reply_markup=kb.report_reasons_kb(f"m:{row.id}"))
        return
    if u.last_partner_id:
        async with session_scope() as s:
            d = (
                await s.execute(
                    select(Dialog).where(
                        ((Dialog.user1_id == u.id) & (Dialog.user2_id == u.last_partner_id))
                        | ((Dialog.user2_id == u.id) & (Dialog.user1_id == u.last_partner_id))
                    ).order_by(Dialog.id.desc())
                )
            ).scalars().first()
        if d:
            await message.answer(
                "🚩 Жалоба на <b>прошлого</b> собеседника:", reply_markup=kb.report_reasons_kb(f"d:{d.id}")
            )
            return
    await message.answer("Не на кого жаловаться 🙂")


@router.callback_query(F.data.startswith("report_d:"))
async def cb_report_dialog(cb: CallbackQuery):
    dialog_id = cb.data.split(":", 1)[1]
    await cb.message.answer(T.REPORT_CHOOSE, reply_markup=kb.report_reasons_kb(f"d:{dialog_id}"))
    await cb.answer()


@router.callback_query(F.data.startswith("rep:"))
async def cb_report(cb: CallbackQuery, user: User, bot: Bot):
    _, kind, target, reason = cb.data.split(":")
    target = int(target)
    reported_id = dialog_id = room_id = None
    async with session_scope() as s:
        if kind == "d":
            dialog = await s.get(Dialog, target)
            if dialog and user.id in (dialog.user1_id, dialog.user2_id):
                reported_id, dialog_id = dialog.partner_of(user.id), dialog.id
        else:
            msg = await s.get(Msg, target)
            if msg and msg.sender_id != user.id:
                reported_id, room_id = msg.sender_id, msg.room_id
        if reported_id is None:
            await cb.answer("Не удалось отправить жалобу")
            return
        dup = (
            await s.execute(
                select(Report.id).where(
                    Report.reporter_id == user.id,
                    Report.reported_id == reported_id,
                    Report.dialog_id == dialog_id if dialog_id else Report.room_id == room_id,
                )
            )
        ).first()
        if dup:
            await cb.answer("Жалоба уже отправлена")
            await cb.message.delete()
            return
        report = Report(
            reporter_id=user.id, reported_id=reported_id, dialog_id=dialog_id, room_id=room_id, reason=reason
        )
        s.add(report)
        await s.flush()
        report_id = report.id
    await cb.message.edit_text(T.REPORT_SENT)
    await cb.answer()
    label = T.REPORT_REASONS.get(reason, reason)
    where = f"диалог #{dialog_id}" if dialog_id else f"комната #{room_id}"
    for admin_id in get_settings().admin_ids:
        await safe_send(
            bot, admin_id,
            f"🚩 <b>Жалоба #{report_id}</b>\n{label}\nНа: <code>{reported_id}</code> · {where}\n"
            f"/ban {reported_id} 24 {reason}",
        )


# ---------- поделиться профилем ----------
@router.message(Command("link"))
@router.message(F.text == T.BTN_SHARE)
async def cmd_share(message: Message, user: User):
    u = await get_user(user.id)
    if u.state != UserState.chatting:
        await message.answer(T.NO_DIALOG.format(search=T.BTN_SEARCH))
        return
    if not message.from_user.username:
        await message.answer(T.SHARE_NO_USERNAME)
        return
    await message.answer(T.SHARE_CONFIRM, reply_markup=kb.share_confirm_kb())


@router.callback_query(F.data == "share:yes")
async def cb_share(cb: CallbackQuery, bot: Bot):
    u = await get_user(cb.from_user.id)
    if u.state != UserState.chatting or not u.current_dialog_id or not cb.from_user.username:
        await cb.answer("Диалог уже завершён", show_alert=True)
        return
    async with session_scope() as s:
        dialog = await s.get(Dialog, u.current_dialog_id)
        partner_id = dialog.partner_of(u.id)
    await safe_send(bot, partner_id, T.SHARE_RECEIVED.format(link=f"@{cb.from_user.username}"))
    await cb.message.edit_text(T.SHARE_SENT)
    await cb.answer()


# ---------- VIP: вернуть прошлого собеседника ----------
@router.message(Command("reconnect"))
async def cmd_reconnect(message: Message, user: User, bot: Bot):
    await _reconnect(bot, user.id, message.answer)


@router.callback_query(F.data == "reconnect")
async def cb_reconnect(cb: CallbackQuery, bot: Bot):
    await cb.answer()
    await cb.message.edit_reply_markup(reply_markup=None)
    await _reconnect(bot, cb.from_user.id, cb.message.answer)


async def _reconnect(bot: Bot, user_id: int, reply) -> None:
    u = await get_user(user_id)
    if not u.is_vip:
        await reply(VIP_ONLY, reply_markup=kb.vip_kb(u))
        return
    if not u.last_partner_id:
        await reply("У тебя ещё не было собеседников.")
        return
    if u.state == UserState.chatting:
        await reply("Сначала заверши текущий диалог.")
        return
    partner = await get_user(u.last_partner_id)
    if partner is None or partner.state not in (UserState.idle, UserState.searching) or partner.is_banned:
        await reply("😔 Прошлый собеседник сейчас занят. Попробуй позже.")
        return
    sent = await safe_send(
        bot, partner.id,
        "🔁 <b>Твой прошлый собеседник хочет продолжить разговор.</b>\nСоединить?",
        reply_markup=kb.reconnect_answer_kb(u.id),
    )
    await reply("📨 Запрос отправлен. Ждём ответа…" if sent else "😔 Собеседник недоступен.")


@router.callback_query(F.data.startswith("recon_"))
async def cb_reconnect_answer(cb: CallbackQuery, bot: Bot):
    action, from_id = cb.data.split(":")
    from_id = int(from_id)
    await cb.message.edit_reply_markup(reply_markup=None)
    if action == "recon_no":
        await cb.answer("Отклонено")
        await safe_send(bot, from_id, "😔 Прошлый собеседник отказался продолжить.")
        return
    u = await get_user(cb.from_user.id)
    if u.last_partner_id != from_id:
        await cb.answer("Запрос устарел", show_alert=True)
        return
    match = await matching.connect_pair(from_id, cb.from_user.id)
    await cb.answer()
    if match is None:
        await cb.message.answer("😔 Не получилось соединить — кто-то из вас уже занят.")
        return
    await notify_match(bot, match)
