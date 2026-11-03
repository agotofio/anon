"""Пересылка сообщений между собеседниками и участниками комнат.

Подключается ПОСЛЕДНИМ роутером — ловит всё, что не перехватили команды и кнопки.
"""
from __future__ import annotations

import asyncio
import logging
import re
from html import escape

from aiogram import Bot, F, Router
from aiogram.enums import ContentType, ParseMode
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError, TelegramRetryAfter
from aiogram.types import Message, ReplyParameters

from ...models import User, UserState
from ...services import messaging, rooms
from ...services.messaging import MEDIA_TYPES
from .. import keyboards as kb
from .. import texts as T
from ..common import finish_dialog, get_user, mark_blocked

router = Router(name="relay")
log = logging.getLogger(__name__)

CAPTIONABLE = {"photo", "video", "animation", "document", "audio", "voice"}
LINK_RE = re.compile(r"(https?://|www\.|t\.me/|telegram\.me/|@[A-Za-z0-9_]{5,})", re.I)


def _ct(message: Message) -> str:
    ct = message.content_type
    return ct.value if hasattr(ct, "value") else str(ct)


def _has_links(message: Message) -> bool:
    for ent in (message.entities or []) + (message.caption_entities or []):
        if ent.type in ("url", "text_link", "mention"):
            return True
    return bool(LINK_RE.search(message.text or message.caption or ""))


async def _resolve_reply(message: Message, sender_id: int) -> messaging.Message | None:
    if not message.reply_to_message:
        return None
    return await messaging.find_message_by_chat_msg(sender_id, message.reply_to_message.message_id)


async def _reply_params(original, recipient_id: int) -> ReplyParameters | None:
    if original is None:
        return None
    tg_id = await messaging.tg_id_for_recipient(original, recipient_id)
    if tg_id is None:
        return None
    return ReplyParameters(message_id=tg_id, allow_sending_without_reply=True)


@router.message(F.content_type.in_({ContentType.SUCCESSFUL_PAYMENT, ContentType.PINNED_MESSAGE}))
async def ignore_service(message: Message):
    return


@router.message()
async def relay(message: Message, user: User, bot: Bot):
    if message.chat.type != "private":
        return
    if message.text and message.text.startswith("/"):
        await message.answer("🤷 Неизвестная команда. Список команд — /help")
        return
    u = await get_user(user.id)
    if u.state == UserState.chatting and u.current_dialog_id:
        await relay_dialog(message, u, bot)
    elif u.state == UserState.in_room and u.current_room_id:
        await relay_room(message, u, bot)
    elif u.state == UserState.searching:
        await message.answer(T.ALREADY_SEARCHING, reply_markup=kb.searching_menu())
    else:
        await message.answer(
            "💡 Сейчас у тебя нет собеседника.\nНажми <b>«🔍 Найти собеседника»</b> или зайди в <b>«👥 Комнаты»</b>.",
            reply_markup=kb.main_menu(),
        )


async def relay_dialog(message: Message, u: User, bot: Bot) -> None:
    from ...db import session_scope
    from ...models import Dialog

    async with session_scope() as s:
        dialog = await s.get(Dialog, u.current_dialog_id)
        partner = await s.get(User, dialog.partner_of(u.id)) if dialog else None
    if dialog is None or partner is None or dialog.ended_at is not None:
        await message.answer(T.NO_DIALOG.format(search=T.BTN_SEARCH), reply_markup=kb.main_menu())
        return

    original = await _resolve_reply(message, u.id)
    row = await messaging.save_message(
        message, u.id, dialog_id=dialog.id, reply_to_id=original.id if original else None
    )
    ct = _ct(message)
    if ct in MEDIA_TYPES and not partner.allow_media:
        await messaging.mark_undelivered(row.id)
        await message.reply(T.PARTNER_NO_MEDIA)
        return

    try:
        sent = await bot.copy_message(
            chat_id=partner.id,
            from_chat_id=message.chat.id,
            message_id=message.message_id,
            reply_parameters=await _reply_params(original, partner.id),
            protect_content=u.is_vip and u.protect_content,
        )
    except TelegramForbiddenError:
        await mark_blocked(partner.id)
        await messaging.mark_undelivered(row.id)
        await message.answer(T.PARTNER_UNAVAILABLE)
        await finish_dialog(bot, u, silent_me=True)
        await message.answer("🏠 Главное меню", reply_markup=kb.main_menu())
        return
    except TelegramBadRequest as e:
        log.info("copy failed: %s", e)
        await messaging.mark_undelivered(row.id)
        await message.reply("⚠️ Этот тип сообщения нельзя переслать.")
        return
    await messaging.save_copies(row.id, [(partner.id, sent.message_id)])


async def relay_room(message: Message, u: User, bot: Bot) -> None:
    member = await rooms.member_of(u.current_room_id, u.id)
    if member is None:
        await message.answer("Ты больше не в комнате.", reply_markup=kb.main_menu())
        return
    if not u.is_vip and _has_links(message):
        await message.reply("🔗 Ссылки и @упоминания в комнатах доступны только 👑 VIP (защита от спама).")
        return

    recipients = [m.user_id for m in await rooms.room_members(u.current_room_id) if m.user_id != u.id]
    original = await _resolve_reply(message, u.id)
    row = await messaging.save_message(
        message, u.id, room_id=u.current_room_id, sender_nick=member.nickname,
        reply_to_id=original.id if original else None,
    )
    if not recipients:
        await message.reply("🦗 В комнате пока никого нет — сообщение сохранено, но его никто не увидел.")
        return

    ct = _ct(message)
    header = f"<b>{escape(member.nickname)}</b>"
    protect = u.is_vip and u.protect_content
    sem = asyncio.Semaphore(15)
    copies: list[tuple[int, int]] = []
    blocked: list[int] = []

    async def deliver(uid: int) -> None:
        async with sem:
            for _ in range(2):
                try:
                    rp = await _reply_params(original, uid)
                    if ct == "text":
                        sent = await bot.send_message(
                            uid, f"{header}\n{message.html_text}", parse_mode=ParseMode.HTML,
                            reply_parameters=rp, protect_content=protect, link_preview_options=None,
                        )
                    elif ct in CAPTIONABLE:
                        caption = header + (f"\n{message.html_text}" if message.caption else "")
                        sent = await bot.copy_message(
                            uid, message.chat.id, message.message_id, caption=caption,
                            parse_mode=ParseMode.HTML, reply_parameters=rp, protect_content=protect,
                        )
                    else:
                        head = await bot.send_message(uid, header + ":", reply_parameters=rp, disable_notification=True)
                        copies.append((uid, head.message_id))
                        sent = await bot.copy_message(uid, message.chat.id, message.message_id, protect_content=protect)
                    copies.append((uid, sent.message_id))
                    return
                except TelegramRetryAfter as e:
                    await asyncio.sleep(e.retry_after)
                except TelegramForbiddenError:
                    blocked.append(uid)
                    return
                except TelegramBadRequest as e:
                    log.info("room delivery to %s failed: %s", uid, e)
                    return

    await asyncio.gather(*(deliver(uid) for uid in recipients))
    await messaging.save_copies(row.id, copies)
    for uid in blocked:
        await mark_blocked(uid)
        await rooms.leave_room(uid)


@router.edited_message()
async def on_edit(message: Message, user: User, bot: Bot):
    new_text = message.text or message.caption
    row = await messaging.register_edit(user.id, message.message_id, new_text)
    if row is None:
        return
    prefix = f"<b>{escape(row.sender_nick)}</b>\n" if row.room_id and row.sender_nick else ""
    body = prefix + (message.html_text or "")
    for copy in await messaging.copies_of(row.id):
        try:
            if message.text is not None:
                await bot.edit_message_text(
                    text=body, chat_id=copy.chat_id, message_id=copy.tg_message_id, parse_mode=ParseMode.HTML
                )
            elif message.caption is not None or row.content_type in CAPTIONABLE:
                await bot.edit_message_caption(
                    caption=body, chat_id=copy.chat_id, message_id=copy.tg_message_id, parse_mode=ParseMode.HTML
                )
        except (TelegramBadRequest, TelegramForbiddenError):
            continue
