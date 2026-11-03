"""Заполняет базу демо-данными, чтобы посмотреть админ-панель без реального бота.

    DATABASE_URL=sqlite+aiosqlite:///data/demo.db python scripts/demo_data.py
"""
from __future__ import annotations

import asyncio
import random
import sys
from datetime import timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from anonchat.db import init_db, session_scope  # noqa: E402
from anonchat.models import (  # noqa: E402
    Dialog, Gender, Message, Payment, Report, Room, RoomMember, User, UserState, utcnow,
)
from anonchat.utils import random_nickname  # noqa: E402

NAMES = ["Аня", "Макс", "Лиза", "Дима", "Катя", "Артём", "Соня", "Илья", "Маша", "Егор", "Даша", "Кирилл"]
PHRASES = [
    "Привет 👋", "Привет! Как дела?", "Норм, а у тебя?", "Тоже неплохо 🙂", "Откуда ты?", "Из Москвы, а ты?",
    "Питер 🌧", "Чем занимаешься?", "Учусь на программиста", "О, круто! Я дизайнер", "Что слушаешь?",
    "Сейчас в репите Земфира", "Классика 🔥", "Посоветуй сериал", "Очень странные дела, если не смотрел(а)",
    "Ахах, точно", "Мне пора, было приятно поболтать!", "Взаимно ✨",
]


async def main() -> None:
    await init_db()
    now = utcnow()
    async with session_scope() as s:
        users = []
        for i in range(60):
            u = User(
                id=100000 + i, username=f"user{i}" if i % 3 else None, first_name=random.choice(NAMES),
                gender=random.choice([Gender.male, Gender.female, None]), age=random.randint(16, 35),
                onboarded=True, created_at=now - timedelta(days=random.randint(0, 29), hours=random.randint(0, 23)),
                last_active=now - timedelta(hours=random.randint(0, 72)), likes=random.randint(0, 30),
                dislikes=random.randint(0, 6), dialogs_count=random.randint(1, 40),
            )
            if i % 9 == 0:
                u.vip_until = now + timedelta(days=random.randint(1, 30))
            if i == 5:
                u.is_banned, u.ban_reason = True, "Спам"
            users.append(u)
            s.add(u)
        await s.flush()

        for d_i in range(40):
            a, b = random.sample(users, 2)
            start = now - timedelta(days=random.randint(0, 29), minutes=random.randint(0, 1000))
            active = d_i < 3
            d = Dialog(user1_id=a.id, user2_id=b.id, started_at=start,
                       ended_at=None if active else start + timedelta(minutes=random.randint(1, 40)),
                       ended_by=None if active else a.id)
            s.add(d)
            await s.flush()
            if active:
                for u in (a, b):
                    u.state, u.current_dialog_id = UserState.chatting, d.id
            n = random.randint(4, len(PHRASES))
            for k in range(n):
                sender = a if k % 2 == 0 else b
                s.add(Message(dialog_id=d.id, sender_id=sender.id, tg_message_id=k + 1, content_type="text",
                              text=PHRASES[k], created_at=start + timedelta(seconds=20 * k)))
            d.messages_count = n

        rooms = (await s.execute(Room.__table__.select())).all()
        for r in rooms[:4]:
            members = random.sample(users, 6)
            for k, u in enumerate(members):
                nick = random_nickname()
                s.add(RoomMember(room_id=r.id, user_id=u.id, nickname=nick))
                if k < 3:
                    u.state, u.current_room_id = UserState.in_room, r.id
                s.add(Message(room_id=r.id, sender_id=u.id, sender_nick=nick, tg_message_id=k + 1,
                              content_type="text", text=random.choice(PHRASES),
                              created_at=now - timedelta(minutes=30 - k)))

        for u in random.sample(users, 3):
            u.state, u.search_since = UserState.searching, now - timedelta(seconds=random.randint(1, 50))

        for k, (code, stars, days) in enumerate([("vip_30d", 249, 30), ("vip_7d", 99, 7), ("donate_100", 100, 3),
                                                  ("vip_forever", 1990, -1), ("vip_1d", 25, 1)] * 3):
            u = random.choice(users)
            u.stars_spent += stars
            s.add(Payment(user_id=u.id, product=code, stars=stars, vip_days=days, charge_id=f"demo_{k}",
                          created_at=now - timedelta(days=random.randint(0, 29))))
        for _ in range(5):
            a, b = random.sample(users, 2)
            s.add(Report(reporter_id=a.id, reported_id=b.id, reason=random.choice(["spam", "abuse", "nsfw"]), dialog_id=1))
    print("Демо-данные готовы")


if __name__ == "__main__":
    asyncio.run(main())
