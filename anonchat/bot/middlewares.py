from __future__ import annotations

import time
from collections import defaultdict, deque
from typing import Any, Awaitable, Callable

from aiogram import BaseMiddleware
from aiogram.types import CallbackQuery, Message, PreCheckoutQuery, TelegramObject

from ..config import get_settings
from ..db import session_scope
from ..services.users import get_or_create_user
from ..utils import fmt_dt
from . import texts as T


class UserMiddleware(BaseMiddleware):
    """Регистрирует/обновляет пользователя, кладёт его в data['user'], отсекает забаненных."""

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        tg_user = data.get("event_from_user")
        if tg_user is None or tg_user.is_bot:
            return await handler(event, data)

        referrer = None
        if isinstance(event, Message) and event.text and event.text.startswith("/start ref_"):
            try:
                referrer = int(event.text.split("ref_", 1)[1])
            except ValueError:
                referrer = None

        async with session_scope() as s:
            user, created = await get_or_create_user(s, tg_user, referrer)
        data["user"] = user
        data["is_new_user"] = created

        if user.is_banned and tg_user.id not in get_settings().admin_ids:
            # оплату не блокируем, чтобы не ломать pre_checkout
            if isinstance(event, PreCheckoutQuery) or (isinstance(event, Message) and event.successful_payment):
                return await handler(event, data)
            until = f"До: {fmt_dt(user.ban_until)} UTC" if user.ban_until else "Бессрочно."
            text = T.BANNED.format(reason=user.ban_reason or "нарушение правил", until=until)
            if isinstance(event, Message):
                await event.answer(text)
            elif isinstance(event, CallbackQuery):
                await event.answer("🚫 Доступ ограничен", show_alert=True)
            return None
        return await handler(event, data)


class FloodMiddleware(BaseMiddleware):
    """Простой антифлуд: не больше N сообщений за окно времени."""

    def __init__(self) -> None:
        s = get_settings()
        self.limit = s.flood_limit
        self.window = s.flood_window
        self.hits: dict[int, deque[float]] = defaultdict(deque)
        self.warned: dict[int, float] = {}

    async def __call__(self, handler, event: Message, data):
        uid = event.from_user.id if event.from_user else 0
        # альбомы приходят пачкой, а платежи нельзя терять — не считаем их флудом
        if event.media_group_id or event.successful_payment:
            return await handler(event, data)
        now = time.monotonic()
        q = self.hits[uid]
        while q and now - q[0] > self.window:
            q.popleft()
        if len(q) >= self.limit:
            if now - self.warned.get(uid, 0) > self.window:
                self.warned[uid] = now
                await event.answer(T.FLOOD)
            return None
        q.append(now)
        return await handler(event, data)
