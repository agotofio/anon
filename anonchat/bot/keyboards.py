from __future__ import annotations

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, KeyboardButton, ReplyKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

from ..config import DONATE_AMOUNTS, DONATE_VIP_BONUS, PRODUCTS
from ..models import Gender, User
from ..services.rooms import RoomInfo
from . import texts as T


def _reply(rows: list[list[str]], placeholder: str | None = None) -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text=t) for t in row] for row in rows],
        resize_keyboard=True,
        input_field_placeholder=placeholder,
    )


def main_menu(user: User | None = None) -> ReplyKeyboardMarkup:
    return _reply(
        [
            [T.BTN_SEARCH],
            [T.BTN_SEARCH_GENDER, T.BTN_ROOMS],
            [T.BTN_PROFILE, T.BTN_VIP],
            [T.BTN_SETTINGS, T.BTN_DONATE, T.BTN_HELP],
        ],
        "Выбери действие…",
    )


def searching_menu() -> ReplyKeyboardMarkup:
    return _reply([[T.BTN_CANCEL_SEARCH]], "Ищем собеседника…")


def chat_menu() -> ReplyKeyboardMarkup:
    return _reply([[T.BTN_NEXT, T.BTN_STOP], [T.BTN_SHARE, T.BTN_REPORT]], "Напиши сообщение…")


def room_menu() -> ReplyKeyboardMarkup:
    return _reply([[T.BTN_MEMBERS, T.BTN_ROOM_INFO], [T.BTN_LEAVE_ROOM]], "Сообщение в комнату…")


def menu_for(user: User) -> ReplyKeyboardMarkup:
    from ..models import UserState

    if user.state == UserState.searching:
        return searching_menu()
    if user.state == UserState.chatting:
        return chat_menu()
    if user.state == UserState.in_room:
        return room_menu()
    return main_menu(user)


# ----- онбординг -----
def gender_kb(prefix: str = "ob_gender") -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="👨 Парень", callback_data=f"{prefix}:male"),
                InlineKeyboardButton(text="👩 Девушка", callback_data=f"{prefix}:female"),
            ],
            [InlineKeyboardButton(text="🤐 Не скажу", callback_data=f"{prefix}:none")],
        ]
    )


AGE_RANGES = [(16, "16–17"), (18, "18–20"), (21, "21–24"), (25, "25–29"), (30, "30–35"), (36, "36+")]


def age_kb(prefix: str = "ob_age") -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    for value, label in AGE_RANGES:
        b.button(text=label, callback_data=f"{prefix}:{value}")
    b.button(text="🤐 Не скажу", callback_data=f"{prefix}:0")
    b.adjust(3, 3, 1)
    return b.as_markup()


# ----- после диалога -----
def rate_kb(dialog_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="👍", callback_data=f"rate:{dialog_id}:1"),
                InlineKeyboardButton(text="👎", callback_data=f"rate:{dialog_id}:-1"),
                InlineKeyboardButton(text="🚩 Жалоба", callback_data=f"report_d:{dialog_id}"),
            ],
            [InlineKeyboardButton(text="🔍 Искать нового", callback_data="search")],
        ]
    )


def after_rate_kb(dialog_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="🚩 Жалоба", callback_data=f"report_d:{dialog_id}")],
            [InlineKeyboardButton(text="🔍 Искать нового", callback_data="search")],
        ]
    )


def report_reasons_kb(target: str) -> InlineKeyboardMarkup:
    """target: 'd:<dialog_id>' или 'm:<message_id>' (сообщение в комнате)."""
    b = InlineKeyboardBuilder()
    for code, label in T.REPORT_REASONS.items():
        b.button(text=label, callback_data=f"rep:{target}:{code}")
    b.button(text="✖️ Отмена", callback_data="close")
    b.adjust(2)
    return b.as_markup()


def share_confirm_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[[
            InlineKeyboardButton(text="✅ Отправить", callback_data="share:yes"),
            InlineKeyboardButton(text="✖️ Отмена", callback_data="close"),
        ]]
    )


# ----- поиск по полу -----
def gender_search_kb(user: User) -> InlineKeyboardMarkup:
    lock = "" if user.is_vip else " 🔒"
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text=f"👩 Девушку{lock}", callback_data="gsearch:female"),
                InlineKeyboardButton(text=f"👨 Парня{lock}", callback_data="gsearch:male"),
            ],
            [InlineKeyboardButton(text="🎲 Кого угодно", callback_data="gsearch:any")],
            [InlineKeyboardButton(text="🎂 Фильтр по возрасту" + lock, callback_data="agefilter")],
        ]
    )


def age_filter_kb() -> InlineKeyboardMarkup:
    ranges = [(16, 17), (18, 24), (18, 30), (25, 35), (30, 99)]
    b = InlineKeyboardBuilder()
    for lo, hi in ranges:
        label = f"{lo}+" if hi == 99 else f"{lo}–{hi}"
        b.button(text=label, callback_data=f"agef:{lo}:{hi}")
    b.button(text="♾ Любой возраст", callback_data="agef:0:0")
    b.adjust(3, 2, 1)
    return b.as_markup()


# ----- комнаты -----
def rooms_kb(rooms: list[RoomInfo], page: int, total: int, per_page: int, user: User) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    for info in rooms:
        r = info.room
        lock = "👑 " if r.vip_only else ""
        b.row(
            InlineKeyboardButton(
                text=f"{r.emoji} {lock}{r.title} · {info.online}/{r.max_members}",
                callback_data=f"room:{r.id}",
            )
        )
    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton(text="◀️", callback_data=f"rooms:{page - 1}"))
    pages = max(1, (total + per_page - 1) // per_page)
    if pages > 1:
        nav.append(InlineKeyboardButton(text=f"{page + 1}/{pages}", callback_data="noop"))
    if (page + 1) * per_page < total:
        nav.append(InlineKeyboardButton(text="▶️", callback_data=f"rooms:{page + 1}"))
    if nav:
        b.row(*nav)
    b.row(
        InlineKeyboardButton(text="🎲 Случайная", callback_data="room_random"),
        InlineKeyboardButton(text="➕ Создать", callback_data="room_create"),
    )
    b.row(InlineKeyboardButton(text="🏠 Мои комнаты", callback_data="my_rooms"))
    return b.as_markup()


def room_card_kb(room_id: int, can_join: bool) -> InlineKeyboardMarkup:
    rows = []
    if can_join:
        rows.append([InlineKeyboardButton(text="🚪 Войти в комнату", callback_data=f"join:{room_id}")])
    rows.append([InlineKeyboardButton(text="◀️ К списку", callback_data="rooms:0")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def my_room_kb(room_id: int, bot_username: str, code: str) -> InlineKeyboardMarkup:
    link = f"https://t.me/{bot_username}?start=room_{code}"
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="🚪 Войти", callback_data=f"join:{room_id}")],
            [InlineKeyboardButton(text="📨 Поделиться ссылкой", url=f"https://t.me/share/url?url={link}")],
            [InlineKeyboardButton(text="🗑 Закрыть комнату", callback_data=f"room_close:{room_id}")],
        ]
    )


def emoji_kb() -> InlineKeyboardMarkup:
    emojis = ["💬", "🔥", "🎮", "🎵", "🎬", "📚", "💔", "😂", "🌙", "⚽", "🍕", "✈️", "🐾", "💻", "🎨", "🧠"]
    b = InlineKeyboardBuilder()
    for e in emojis:
        b.button(text=e, callback_data=f"rc_emoji:{e}")
    b.adjust(8)
    return b.as_markup()


def room_privacy_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[[
            InlineKeyboardButton(text="🌍 Публичная", callback_data="rc_pub:1"),
            InlineKeyboardButton(text="🔒 По ссылке", callback_data="rc_pub:0"),
        ]]
    )


def room_size_kb() -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    for n in (5, 10, 30, 50, 100):
        b.button(text=str(n), callback_data=f"rc_size:{n}")
    b.adjust(5)
    return b.as_markup()


# ----- VIP / донаты -----
def vip_kb(user: User) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    for p in PRODUCTS.values():
        b.button(text=f"{p.emoji} {p.title} — {p.stars} ⭐", callback_data=f"buy:{p.code}")
    b.button(text="🎁 Получить VIP бесплатно", callback_data="invite")
    b.adjust(1)
    return b.as_markup()


def donate_kb() -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    for amount in DONATE_AMOUNTS:
        bonus = DONATE_VIP_BONUS.get(amount)
        label = f"{amount} ⭐" + (f" (+{bonus}д VIP)" if bonus else "")
        b.button(text=label, callback_data=f"donate:{amount}")
    b.adjust(3)
    return b.as_markup()


def reconnect_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="🔁 Вернуть прошлого собеседника", callback_data="reconnect")]]
    )


def reconnect_answer_kb(from_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[[
            InlineKeyboardButton(text="✅ Продолжить", callback_data=f"recon_yes:{from_id}"),
            InlineKeyboardButton(text="✖️ Нет", callback_data=f"recon_no:{from_id}"),
        ]]
    )


# ----- профиль / настройки -----
def profile_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="✏️ Пол", callback_data="edit_gender"),
                InlineKeyboardButton(text="✏️ Возраст", callback_data="edit_age"),
            ],
            [InlineKeyboardButton(text="🎁 Пригласить друга", callback_data="invite")],
        ]
    )


def settings_kb(user: User) -> InlineKeyboardMarkup:
    def onoff(v: bool) -> str:
        return "✅" if v else "❌"

    vip_lock = "" if user.is_vip else " 🔒"
    pref = {Gender.male: "👨 парни", Gender.female: "👩 девушки"}.get(user.pref_gender, "🎲 все")
    if user.pref_age_min or user.pref_age_max:
        age = f"{user.pref_age_min or '…'}–{user.pref_age_max or '…'}"
    else:
        age = "любой"
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=f"{onoff(user.show_profile)} Показывать пол и возраст", callback_data="set:show_profile")],
            [InlineKeyboardButton(text=f"{onoff(user.allow_media)} Принимать медиа", callback_data="set:allow_media")],
            [InlineKeyboardButton(text=f"{onoff(user.protect_content)} Защита от пересылки{vip_lock}", callback_data="set:protect_content")],
            [InlineKeyboardButton(text=f"🎯 Ищу: {pref}{vip_lock}", callback_data="set:pref_gender")],
            [InlineKeyboardButton(text=f"🎂 Возраст собеседника: {age}{vip_lock}", callback_data="agefilter")],
        ]
    )


def admin_kb(url: str | None) -> InlineKeyboardMarkup | None:
    if not url:
        return None
    return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="🖥 Открыть админ-панель", url=url)]])
