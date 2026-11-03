"""Сохранение сообщений и связка оригинал ↔ копии (для ответов и правок)."""
from __future__ import annotations

from aiogram.types import Message as TgMessage
from sqlalchemy import select, update

from ..db import session_scope
from ..models import Dialog, Message, MessageCopy, Room, User

MEDIA_TYPES = {"photo", "video", "animation", "document", "audio", "voice", "video_note", "sticker"}


def extract_content(msg: TgMessage) -> dict:
    ct = msg.content_type
    ct = ct.value if hasattr(ct, "value") else str(ct)
    data: dict = {"content_type": ct, "text": msg.text or msg.caption}
    file = None
    if msg.photo:
        file = msg.photo[-1]
        data["mime_type"] = "image/jpeg"
    elif msg.video:
        file = msg.video
        data["mime_type"] = msg.video.mime_type or "video/mp4"
        data["file_name"] = msg.video.file_name
    elif msg.animation:
        file = msg.animation
        data["mime_type"] = msg.animation.mime_type or "video/mp4"
        data["file_name"] = msg.animation.file_name
    elif msg.document:
        file = msg.document
        data["mime_type"] = msg.document.mime_type
        data["file_name"] = msg.document.file_name
    elif msg.audio:
        file = msg.audio
        data["mime_type"] = msg.audio.mime_type or "audio/mpeg"
        data["file_name"] = msg.audio.file_name or " — ".join(
            x for x in (msg.audio.performer, msg.audio.title) if x
        ) or None
    elif msg.voice:
        file = msg.voice
        data["mime_type"] = msg.voice.mime_type or "audio/ogg"
    elif msg.video_note:
        file = msg.video_note
        data["mime_type"] = "video/mp4"
    elif msg.sticker:
        file = msg.sticker
        if msg.sticker.is_animated:
            data["mime_type"] = "application/x-tgsticker"
        elif msg.sticker.is_video:
            data["mime_type"] = "video/webm"
        else:
            data["mime_type"] = "image/webp"
        data["text"] = msg.sticker.emoji
    elif msg.location:
        data["text"] = f"📍 {msg.location.latitude}, {msg.location.longitude}"
    elif msg.contact:
        data["text"] = f"👤 {msg.contact.first_name} {msg.contact.phone_number}"
    elif msg.dice:
        data["text"] = f"{msg.dice.emoji} {msg.dice.value}"
    elif msg.poll:
        data["text"] = "📊 " + msg.poll.question + "\n" + "\n".join(f"• {o.text}" for o in msg.poll.options)
    if file is not None:
        data["file_id"] = file.file_id
        data["file_unique_id"] = file.file_unique_id
    return data


async def save_message(
    msg: TgMessage,
    sender_id: int,
    *,
    dialog_id: int | None = None,
    room_id: int | None = None,
    sender_nick: str | None = None,
    reply_to_id: int | None = None,
) -> Message:
    async with session_scope() as s:
        row = Message(
            dialog_id=dialog_id,
            room_id=room_id,
            sender_id=sender_id,
            sender_nick=sender_nick,
            tg_message_id=msg.message_id,
            reply_to_id=reply_to_id,
            **extract_content(msg),
        )
        s.add(row)
        await s.execute(update(User).where(User.id == sender_id).values(messages_count=User.messages_count + 1))
        if dialog_id:
            await s.execute(
                update(Dialog).where(Dialog.id == dialog_id).values(messages_count=Dialog.messages_count + 1)
            )
        if room_id:
            await s.execute(update(Room).where(Room.id == room_id).values(messages_count=Room.messages_count + 1))
        await s.flush()
        return row


async def mark_undelivered(message_id: int) -> None:
    async with session_scope() as s:
        await s.execute(update(Message).where(Message.id == message_id).values(delivered=False))


async def save_copies(message_id: int, copies: list[tuple[int, int]]) -> None:
    if not copies:
        return
    async with session_scope() as s:
        s.add_all(MessageCopy(message_id=message_id, chat_id=c, tg_message_id=m) for c, m in copies)


async def find_message_by_chat_msg(chat_id: int, tg_message_id: int) -> Message | None:
    """Находит сообщение по id в конкретном чате (оригинал отправителя или доставленная копия)."""
    async with session_scope() as s:
        original = (
            await s.execute(
                select(Message).where(Message.sender_id == chat_id, Message.tg_message_id == tg_message_id)
                .order_by(Message.id.desc())
            )
        ).scalars().first()
        if original:
            return original
        copy = (
            await s.execute(
                select(MessageCopy).where(MessageCopy.chat_id == chat_id, MessageCopy.tg_message_id == tg_message_id)
                .order_by(MessageCopy.id.desc())
            )
        ).scalars().first()
        if copy:
            return await s.get(Message, copy.message_id)
        return None


async def tg_id_for_recipient(message: Message, recipient_id: int) -> int | None:
    """id сообщения `message` в чате `recipient_id`."""
    if message.sender_id == recipient_id:
        return message.tg_message_id
    async with session_scope() as s:
        return (
            await s.execute(
                select(MessageCopy.tg_message_id).where(
                    MessageCopy.message_id == message.id, MessageCopy.chat_id == recipient_id
                )
            )
        ).scalars().first()


async def copies_of(message_id: int) -> list[MessageCopy]:
    async with session_scope() as s:
        return list((await s.execute(select(MessageCopy).where(MessageCopy.message_id == message_id))).scalars())


async def register_edit(sender_id: int, tg_message_id: int, new_text: str | None) -> Message | None:
    async with session_scope() as s:
        row = (
            await s.execute(
                select(Message).where(Message.sender_id == sender_id, Message.tg_message_id == tg_message_id)
                .order_by(Message.id.desc())
            )
        ).scalars().first()
        if row is None:
            return None
        if not row.edited:
            row.original_text = row.text
        row.edited = True
        row.text = new_text
        return row
