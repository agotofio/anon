from __future__ import annotations

import random
import secrets
import string
from datetime import datetime

from .models import Gender, User, utcnow

ADJECTIVES = [
    "Хитрый", "Сонный", "Весёлый", "Тихий", "Смелый", "Загадочный", "Добрый", "Дикий",
    "Ночной", "Ленивый", "Быстрый", "Мудрый", "Пушистый", "Космический", "Солнечный",
    "Неоновый", "Задумчивый", "Дерзкий", "Мятный", "Бархатный", "Шальной", "Лунный",
]
ANIMALS = [
    ("🦊", "Лис"), ("🐼", "Панда"), ("🐺", "Волк"), ("🦉", "Филин"), ("🐱", "Кот"),
    ("🐻", "Медведь"), ("🐧", "Пингвин"), ("🦁", "Лев"), ("🐨", "Коала"), ("🐯", "Тигр"),
    ("🦝", "Енот"), ("🐸", "Лягух"), ("🐙", "Осьминог"), ("🦄", "Единорог"), ("🐬", "Дельфин"),
    ("🦖", "Динозавр"), ("🐢", "Черепах"), ("🦜", "Попугай"), ("🐝", "Шмель"), ("🦔", "Ёж"),
]


def random_nickname() -> str:
    emoji, animal = random.choice(ANIMALS)
    adj = random.choice(ADJECTIVES)
    # Согласуем род для пары «женских» животных
    if animal in {"Панда", "Коала"}:
        adj = adj[:-2] + "ая" if adj.endswith(("ый", "ий", "ой")) else adj
    return f"{emoji} {adj} {animal}"


def invite_code(length: int = 8) -> str:
    alphabet = string.ascii_letters + string.digits
    return "".join(secrets.choice(alphabet) for _ in range(length))


def gender_label(g: Gender | None, short: bool = False) -> str:
    if g == Gender.male:
        return "👨" if short else "👨 Парень"
    if g == Gender.female:
        return "👩" if short else "👩 Девушка"
    return "🤷" if short else "🤷 Не указан"


def plural(n: int, one: str, few: str, many: str) -> str:
    n_abs = abs(n) % 100
    if 11 <= n_abs <= 19:
        return many
    n1 = n_abs % 10
    if n1 == 1:
        return one
    if 2 <= n1 <= 4:
        return few
    return many


def vip_status_text(user: User) -> str:
    if user.vip_forever:
        return "💎 VIP навсегда"
    if user.is_vip and user.vip_until:
        left = user.vip_until - utcnow()
        days = left.days
        hours = left.seconds // 3600
        if days > 0:
            return f"👑 VIP ещё {days} {plural(days, 'день', 'дня', 'дней')}"
        return f"👑 VIP ещё {hours} ч."
    return "—"


def fmt_dt(dt: datetime | None) -> str:
    if not dt:
        return "—"
    return dt.strftime("%d.%m.%Y %H:%M")


def rating_text(user: User) -> str:
    r = user.rating
    if r is None:
        return "нет оценок"
    return f"⭐ {r} ({user.likes}👍 / {user.dislikes}👎)"
