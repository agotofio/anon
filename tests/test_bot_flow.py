"""Сквозной тест бота: апдейты подаются в Dispatcher, запросы к Telegram перехватываются."""
import itertools
from datetime import datetime

import pytest
from aiogram import Bot
from aiogram.client.session.base import BaseSession
from aiogram.methods import (
    AnswerPreCheckoutQuery, CopyMessage, EditMessageText, GetMe,
    SendInvoice, SendMessage, TelegramMethod,
)
from aiogram.types import Chat, Message, MessageId, Update, User as TgUser

from anonchat.bot.setup import build_dispatcher
from anonchat.db import engine, init_db, session_scope
from anonchat.models import Base, Dialog, Message as Msg, Payment, User, UserState

from .test_core import run

_ids = itertools.count(1000)


class FakeSession(BaseSession):
    def __init__(self):
        super().__init__()
        self.calls: list[TelegramMethod] = []

    async def make_request(self, bot, method, timeout=None):
        self.calls.append(method)
        if isinstance(method, GetMe):
            return TgUser(id=1, is_bot=True, first_name="Bot", username="anon_test_bot")
        if isinstance(method, CopyMessage):
            return MessageId(message_id=next(_ids))
        if isinstance(method, (SendMessage, SendInvoice, EditMessageText)):
            chat_id = getattr(method, "chat_id", 0) or 0
            return Message(message_id=next(_ids), date=datetime.now(), chat=Chat(id=chat_id, type="private"),
                           text=getattr(method, "text", None))
        return True

    async def close(self):
        pass

    async def stream_content(self, *a, **k):
        yield b""

    def texts_to(self, chat_id):
        return [c.text for c in self.calls if isinstance(c, SendMessage) and c.chat_id == chat_id]


@pytest.fixture
def env():
    async def reset():
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.drop_all)
        await init_db()
    run(reset())
    session = FakeSession()
    bot = Bot("123:TEST", session=session)
    dp = build_dispatcher()
    return bot, dp, session


_upd = itertools.count(1)


def msg_update(uid, text=None, **extra):
    data = {"message_id": next(_ids), "date": datetime.now(), "chat": {"id": uid, "type": "private"},
            "from": {"id": uid, "is_bot": False, "first_name": f"U{uid}", "username": f"user{uid}"}}
    if text is not None:
        data["text"] = text
        if text.startswith("/"):
            data["entities"] = [{"type": "bot_command", "offset": 0, "length": len(text.split()[0])}]
    data.update(extra)
    return Update(update_id=next(_upd), message=data)


def cb_update(uid, data):
    return Update(update_id=next(_upd), callback_query={
        "id": str(next(_ids)), "chat_instance": "x", "data": data,
        "from": {"id": uid, "is_bot": False, "first_name": f"U{uid}"},
        "message": {"message_id": next(_ids), "date": datetime.now(), "chat": {"id": uid, "type": "private"}, "text": "x"},
    })


async def get(uid):
    async with session_scope() as s:
        return await s.get(User, uid)


def test_full_flow(env):
    bot, dp, session = env

    async def feed(u):
        await dp.feed_update(bot, u)

    async def scenario():
        # онбординг двух пользователей
        for uid, g in ((11, "male"), (22, "female")):
            await feed(msg_update(uid, "/start"))
            await feed(cb_update(uid, f"ob_gender:{g}"))
            await feed(cb_update(uid, "ob_age:21"))
            assert (await get(uid)).onboarded
        # поиск
        await feed(msg_update(11, "🔍 Найти собеседника"))
        assert (await get(11)).state == UserState.searching
        await feed(msg_update(22, "/search"))
        assert (await get(11)).state == UserState.chatting
        assert any("Собеседник найден" in (t or "") for t in session.texts_to(11))
        # переписка
        session.calls.clear()
        await feed(msg_update(11, "Привет!"))
        copies = [c for c in session.calls if isinstance(c, CopyMessage)]
        assert copies and copies[0].chat_id == 22
        await feed(msg_update(22, None, photo=[{"file_id": "PH", "file_unique_id": "u1", "width": 1, "height": 1}],
                              caption="фото"))
        async with session_scope() as s:
            from sqlalchemy import select
            rows = (await s.execute(select(Msg).order_by(Msg.id))).scalars().all()
        assert [r.content_type for r in rows] == ["text", "photo"] and rows[1].file_id == "PH"
        # неизвестная команда не уходит собеседнику
        session.calls.clear()
        await feed(msg_update(11, "/whatever"))
        assert not [c for c in session.calls if isinstance(c, CopyMessage)]
        # стоп + оценка
        await feed(msg_update(11, "⛔ Стоп"))
        assert (await get(11)).state == UserState.idle and (await get(22)).state == UserState.idle
        async with session_scope() as s:
            d = await s.get(Dialog, 1)
            assert d.ended_at is not None and d.messages_count == 2
        await feed(cb_update(11, "rate:1:1"))
        assert (await get(22)).likes == 1
        # комнаты
        await feed(msg_update(11, "/rooms"))
        await feed(cb_update(11, "join:1"))
        await feed(cb_update(22, "join:1"))
        assert (await get(11)).state == UserState.in_room
        session.calls.clear()
        await feed(msg_update(22, "всем привет"))
        sent = [c for c in session.calls if isinstance(c, SendMessage) and c.chat_id == 11]
        assert sent and "всем привет" in sent[-1].text
        # ссылки от не-VIP блокируются
        session.calls.clear()
        await feed(msg_update(22, "заходите t.me/spam"))
        assert not [c for c in session.calls if isinstance(c, SendMessage) and c.chat_id == 11]
        # VIP-only функции закрыты
        await feed(cb_update(11, "gsearch:female"))
        assert any("VIP" in (t or "") for t in session.texts_to(11)[-1:])
        # оплата Stars
        await feed(cb_update(11, "buy:vip_30d"))
        inv = [c for c in session.calls if isinstance(c, SendInvoice)][-1]
        assert inv.currency == "XTR" and inv.prices[0].amount == 249
        await feed(Update(update_id=next(_upd), pre_checkout_query={
            "id": "q1", "from": {"id": 11, "is_bot": False, "first_name": "U"}, "currency": "XTR",
            "total_amount": 249, "invoice_payload": "vip:vip_30d"}))
        pc = [c for c in session.calls if isinstance(c, AnswerPreCheckoutQuery)][-1]
        assert pc.ok
        await feed(msg_update(11, None, successful_payment={
            "currency": "XTR", "total_amount": 249, "invoice_payload": "vip:vip_30d",
            "telegram_payment_charge_id": "ch1", "provider_payment_charge_id": ""}))
        u = await get(11)
        assert u.is_vip and u.stars_spent == 249
        async with session_scope() as s:
            assert (await s.get(Payment, 1)).charge_id == "ch1"
        # теперь фильтр по полу работает
        await feed(msg_update(11, "/leave"))
        await feed(cb_update(11, "gsearch:female"))
        u = await get(11)
        assert u.state == UserState.searching and u.pref_gender.value == "female"

    run(scenario())
