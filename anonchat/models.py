from __future__ import annotations

import enum
from datetime import datetime, timezone

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


class Base(DeclarativeBase):
    pass


class Gender(str, enum.Enum):
    male = "male"
    female = "female"


class UserState(str, enum.Enum):
    idle = "idle"
    searching = "searching"
    chatting = "chatting"
    in_room = "in_room"


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)  # Telegram user id
    username: Mapped[str | None] = mapped_column(String(64))
    first_name: Mapped[str | None] = mapped_column(String(128))
    language: Mapped[str | None] = mapped_column(String(8))

    gender: Mapped[Gender | None] = mapped_column(Enum(Gender))
    age: Mapped[int | None] = mapped_column(SmallInteger)
    onboarded: Mapped[bool] = mapped_column(Boolean, default=False)

    # Поиск
    state: Mapped[UserState] = mapped_column(Enum(UserState), default=UserState.idle, index=True)
    search_since: Mapped[datetime | None] = mapped_column(DateTime)
    pref_gender: Mapped[Gender | None] = mapped_column(Enum(Gender))   # VIP: кого искать
    pref_age_min: Mapped[int | None] = mapped_column(SmallInteger)     # VIP
    pref_age_max: Mapped[int | None] = mapped_column(SmallInteger)     # VIP
    current_dialog_id: Mapped[int | None] = mapped_column(Integer)
    current_room_id: Mapped[int | None] = mapped_column(Integer)
    last_partner_id: Mapped[int | None] = mapped_column(BigInteger)

    # Настройки
    show_profile: Mapped[bool] = mapped_column(Boolean, default=True)   # показывать пол/возраст собеседнику
    protect_content: Mapped[bool] = mapped_column(Boolean, default=False)  # VIP: запрет пересылки/сохранения
    allow_media: Mapped[bool] = mapped_column(Boolean, default=True)    # принимать медиа от собеседника

    # VIP / монетизация
    vip_until: Mapped[datetime | None] = mapped_column(DateTime)
    vip_forever: Mapped[bool] = mapped_column(Boolean, default=False)
    stars_spent: Mapped[int] = mapped_column(Integer, default=0)

    # Репутация
    likes: Mapped[int] = mapped_column(Integer, default=0)
    dislikes: Mapped[int] = mapped_column(Integer, default=0)

    # Статистика
    dialogs_count: Mapped[int] = mapped_column(Integer, default=0)
    messages_count: Mapped[int] = mapped_column(Integer, default=0)

    # Рефералы
    referrer_id: Mapped[int | None] = mapped_column(BigInteger)
    referrals_count: Mapped[int] = mapped_column(Integer, default=0)

    # Модерация
    is_banned: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    ban_reason: Mapped[str | None] = mapped_column(String(255))
    ban_until: Mapped[datetime | None] = mapped_column(DateTime)
    bot_blocked: Mapped[bool] = mapped_column(Boolean, default=False)  # пользователь заблокировал бота

    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)
    last_active: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)

    @property
    def is_vip(self) -> bool:
        return self.vip_forever or (self.vip_until is not None and self.vip_until > utcnow())

    @property
    def rating(self) -> float | None:
        total = self.likes + self.dislikes
        if total == 0:
            return None
        return round(5 * self.likes / total, 1)

    @property
    def display_name(self) -> str:
        if self.username:
            return f"@{self.username}"
        return self.first_name or str(self.id)


class Dialog(Base):
    """Диалог один на один."""

    __tablename__ = "dialogs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user1_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    user2_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    started_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime, index=True)
    ended_by: Mapped[int | None] = mapped_column(BigInteger)
    messages_count: Mapped[int] = mapped_column(Integer, default=0)

    user1: Mapped[User] = relationship(foreign_keys=[user1_id], lazy="joined")
    user2: Mapped[User] = relationship(foreign_keys=[user2_id], lazy="joined")

    @property
    def is_active(self) -> bool:
        return self.ended_at is None

    def partner_of(self, user_id: int) -> int:
        return self.user2_id if self.user1_id == user_id else self.user1_id


class Room(Base):
    """Групповая анонимная комната."""

    __tablename__ = "rooms"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    title: Mapped[str] = mapped_column(String(64))
    emoji: Mapped[str] = mapped_column(String(8), default="💬")
    description: Mapped[str | None] = mapped_column(String(255))
    is_public: Mapped[bool] = mapped_column(Boolean, default=True)
    vip_only: Mapped[bool] = mapped_column(Boolean, default=False)
    max_members: Mapped[int] = mapped_column(Integer, default=30)
    invite_code: Mapped[str] = mapped_column(String(16), unique=True, index=True)
    owner_id: Mapped[int | None] = mapped_column(BigInteger)  # None — системная комната
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    sort_order: Mapped[int] = mapped_column(Integer, default=100)
    messages_count: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class RoomMember(Base):
    __tablename__ = "room_members"
    __table_args__ = (Index("ix_room_members_active", "room_id", "left_at"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    room_id: Mapped[int] = mapped_column(ForeignKey("rooms.id", ondelete="CASCADE"), index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    nickname: Mapped[str] = mapped_column(String(48))
    joined_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    left_at: Mapped[datetime | None] = mapped_column(DateTime)
    kicked: Mapped[bool] = mapped_column(Boolean, default=False)


class Message(Base):
    """Каждое сообщение, прошедшее через бота (для модерации и админ-панели)."""

    __tablename__ = "messages"
    __table_args__ = (
        Index("ix_messages_sender_tg", "sender_id", "tg_message_id"),
        Index("ix_messages_dialog", "dialog_id", "id"),
        Index("ix_messages_room", "room_id", "id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    dialog_id: Mapped[int | None] = mapped_column(ForeignKey("dialogs.id", ondelete="CASCADE"))
    room_id: Mapped[int | None] = mapped_column(ForeignKey("rooms.id", ondelete="CASCADE"))
    sender_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    sender_nick: Mapped[str | None] = mapped_column(String(48))
    tg_message_id: Mapped[int] = mapped_column(BigInteger)
    content_type: Mapped[str] = mapped_column(String(24), index=True)
    text: Mapped[str | None] = mapped_column(Text)
    file_id: Mapped[str | None] = mapped_column(String(255))
    file_unique_id: Mapped[str | None] = mapped_column(String(64))
    file_name: Mapped[str | None] = mapped_column(String(255))
    mime_type: Mapped[str | None] = mapped_column(String(128))
    reply_to_id: Mapped[int | None] = mapped_column(Integer)
    edited: Mapped[bool] = mapped_column(Boolean, default=False)
    original_text: Mapped[str | None] = mapped_column(Text)
    delivered: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)

    sender: Mapped[User] = relationship(lazy="joined")


class MessageCopy(Base):
    """Куда и под каким id доставлена копия сообщения (нужно для ответов и правок)."""

    __tablename__ = "message_copies"
    __table_args__ = (Index("ix_copies_chat_msg", "chat_id", "tg_message_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    message_id: Mapped[int] = mapped_column(ForeignKey("messages.id", ondelete="CASCADE"), index=True)
    chat_id: Mapped[int] = mapped_column(BigInteger)
    tg_message_id: Mapped[int] = mapped_column(BigInteger)


class Rating(Base):
    __tablename__ = "ratings"
    __table_args__ = (UniqueConstraint("dialog_id", "from_user_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    dialog_id: Mapped[int] = mapped_column(ForeignKey("dialogs.id", ondelete="CASCADE"))
    from_user_id: Mapped[int] = mapped_column(BigInteger, index=True)
    to_user_id: Mapped[int] = mapped_column(BigInteger, index=True)
    value: Mapped[int] = mapped_column(SmallInteger)  # +1 / -1
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class ReportStatus(str, enum.Enum):
    open = "open"
    resolved = "resolved"
    rejected = "rejected"


class Report(Base):
    __tablename__ = "reports"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    reporter_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    reported_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    dialog_id: Mapped[int | None] = mapped_column(Integer)
    room_id: Mapped[int | None] = mapped_column(Integer)
    reason: Mapped[str] = mapped_column(String(64))
    status: Mapped[ReportStatus] = mapped_column(Enum(ReportStatus), default=ReportStatus.open, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    reporter: Mapped[User] = relationship(foreign_keys=[reporter_id], lazy="joined")
    reported: Mapped[User] = relationship(foreign_keys=[reported_id], lazy="joined")


class Payment(Base):
    __tablename__ = "payments"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    product: Mapped[str] = mapped_column(String(32))
    stars: Mapped[int] = mapped_column(Integer)
    vip_days: Mapped[int] = mapped_column(Integer, default=0)
    charge_id: Mapped[str] = mapped_column(String(255), unique=True)
    refunded: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)

    user: Mapped[User] = relationship(lazy="joined")


class Broadcast(Base):
    __tablename__ = "broadcasts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    text: Mapped[str] = mapped_column(Text)
    target: Mapped[str] = mapped_column(String(16), default="all")
    sent: Mapped[int] = mapped_column(Integer, default=0)
    failed: Mapped[int] = mapped_column(Integer, default=0)
    finished: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
