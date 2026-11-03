"""Общие функции бота: уведомления о матче, завершение диалога, выход из комнаты."""
from __future__ import annotations

import asyncio
import logging
from html import escape

from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError, TelegramRetryAfter

from ..db import session_scope
from ..models import User
from ..services import matching, rooms
from ..utils import gender_label, rating_text
from . import keyboards as kb
from . import texts as T

log = logging.getLogger(__name__)


async def safe_send(bot: Bot, chat_id: int, text: str, **kwargs):
    """send_message, который не падает, если пользователь заблокировал бота."""
    for _ in range(3):
        try:
            return await bot.send_message(chat_id, text, **kwargs)
        except TelegramRetryAfter as e:
            await asyncio.sleep(e.retry_after)
        except TelegramForbiddenError:
            await mark_blocked(chat_id)
            return None
        except TelegramBadRequest as e:
            log.warning("send to %s failed: %s", chat_id, e)
            return None
    return None


async def mark_blocked(user_id: int) -> None:
    async with session_scope() as s:
        u = await s.get(User, user_id)
        if u:
            u.bot_blocked = True


async def get_user(user_id: int) -> User | None:
    async with session_scope() as s:
        return await s.get(User, user_id)


def partner_card(partner: User, viewer: User) -> str:
    lines = []
    if partner.show_profile and (partner.gender or partner.age):
        info = gender_label(partner.gender)
        if partner.age:
            info += f", {partner.age}"
        lines.append(info)
    else:
        lines.append("🎭 Профиль скрыт")
    if partner.is_vip:
        lines.append("👑 VIP-пользователь")
    if viewer.is_vip:
        lines.append(f"Рейтинг: {rating_text(partner)}")
    else:
        lines.append("<i>Рейтинг собеседника видят 👑 VIP</i>")
    return "\n".join(lines)


async def notify_match(bot: Bot, match: matching.Match) -> None:
    pairs = ((match.user_a, match.user_b), (match.user_b, match.user_a))
    for me, partner in pairs:
        sent = await safe_send(
            bot, me.id, T.FOUND.format(partner=partner_card(partner, me)), reply_markup=kb.chat_menu()
        )
        if sent is None:
            # пользователь недоступен — разрываем и возвращаем второго в поиск
            await matching.end_dialog(me.id)
            await safe_send(bot, partner.id, T.PARTNER_UNAVAILABLE, reply_markup=kb.main_menu())
            m = await matching.start_search(partner.id)
            if m:
                await notify_match(bot, m)
            else:
                await safe_send(bot, partner.id, "🔎 Ищем нового собеседника…", reply_markup=kb.searching_menu())
            return


async def finish_dialog(bot: Bot, user: User, *, silent_me: bool = False) -> bool:
    """Завершает диалог пользователя и уведомляет обоих. True, если диалог был."""
    result = await matching.end_dialog(user.id)
    if result is None:
        return False
    dialog, partner_id = result
    if not silent_me:
        await bot.send_message(user.id, T.DIALOG_ENDED_ME, reply_markup=kb.rate_kb(dialog.id))
    await safe_send(bot, partner_id, T.DIALOG_ENDED_PARTNER, reply_markup=kb.rate_kb(dialog.id))
    partner = await get_user(partner_id)
    if partner:
        await safe_send(bot, partner_id, _menu_hint(partner), reply_markup=kb.main_menu())
    return True


def _menu_hint(user: User) -> str:
    text = "🏠 Ты в главном меню."
    if user.is_vip:
        text += "\n👑 Можно вернуть собеседника: /reconnect"
    return text


async def leave_current_room(bot: Bot, user: User, *, notify_me: bool = True) -> bool:
    res = await rooms.leave_room(user.id)
    if res is None:
        return False
    room, member, others = res
    await broadcast_service(bot, others, f"➖ <b>{escape(member.nickname)}</b> вышел(а) из комнаты")
    if notify_me:
        await bot.send_message(
            user.id, f"🚪 Ты вышел(а) из комнаты {room.emoji} <b>{escape(room.title)}</b>", reply_markup=kb.main_menu()
        )
    return True


async def broadcast_service(bot: Bot, user_ids: list[int], text: str) -> None:
    sem = asyncio.Semaphore(20)

    async def one(uid: int):
        async with sem:
            await safe_send(bot, uid, text, disable_notification=True)

    await asyncio.gather(*(one(u) for u in user_ids))


async def matcher_loop(bot: Bot, interval: float = 5.0) -> None:
    """Фоновая задача: периодически пересматривает очередь поиска."""
    while True:
        try:
            for m in await matching.match_pending():
                await notify_match(bot, m)
        except asyncio.CancelledError:
            raise
        except Exception:  # pragma: no cover
            log.exception("matcher loop error")
        await asyncio.sleep(interval)
