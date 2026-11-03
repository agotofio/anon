"""Групповые анонимные комнаты."""
from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import session_scope
from ..models import Room, RoomMember, User, UserState, utcnow
from ..utils import invite_code, random_nickname

DEFAULT_ROOMS = [
    ("💬", "Просто поболтать", "Общий чат обо всём на свете", False, 10),
    ("🌙", "Ночной чат", "Для тех, кому не спится", False, 20),
    ("🎮", "Игры", "Игры, стримы, тиммейты", False, 30),
    ("🎵", "Музыка", "Делимся треками и вкусами", False, 40),
    ("🎬", "Кино и сериалы", "Что посмотреть вечером", False, 50),
    ("💔", "Отношения", "Советы, истории, поддержка", False, 60),
    ("📚", "Учёба и работа", "Помощь, мотивация, нытьё о дедлайнах", False, 70),
    ("😂", "Мемы", "Только смешное", False, 80),
    ("👑", "VIP-лаунж", "Закрытый клуб для VIP", True, 5),
]


class RoomError(Exception):
    pass


@dataclass
class RoomInfo:
    room: Room
    online: int


async def seed_default_rooms() -> None:
    async with session_scope() as s:
        has_rooms = (await s.execute(select(func.count()).select_from(Room))).scalar_one()
        if has_rooms:
            return
        for emoji, title, desc, vip_only, order in DEFAULT_ROOMS:
            s.add(
                Room(
                    emoji=emoji,
                    title=title,
                    description=desc,
                    vip_only=vip_only,
                    max_members=100,
                    invite_code=invite_code(),
                    sort_order=order,
                )
            )


async def online_count(s: AsyncSession, room_id: int) -> int:
    return (
        await s.execute(
            select(func.count()).select_from(RoomMember).where(
                RoomMember.room_id == room_id, RoomMember.left_at.is_(None)
            )
        )
    ).scalar_one()


async def list_public_rooms(offset: int = 0, limit: int = 8) -> tuple[list[RoomInfo], int]:
    async with session_scope() as s:
        online_sq = (
            select(RoomMember.room_id, func.count().label("online"))
            .where(RoomMember.left_at.is_(None))
            .group_by(RoomMember.room_id)
            .subquery()
        )
        base = select(Room, func.coalesce(online_sq.c.online, 0)).outerjoin(
            online_sq, online_sq.c.room_id == Room.id
        ).where(Room.is_active.is_(True), Room.is_public.is_(True))
        total = (
            await s.execute(
                select(func.count()).select_from(Room).where(Room.is_active.is_(True), Room.is_public.is_(True))
            )
        ).scalar_one()
        rows = (
            await s.execute(
                base.order_by(Room.vip_only.desc(), func.coalesce(online_sq.c.online, 0).desc(), Room.sort_order)
                .offset(offset)
                .limit(limit)
            )
        ).all()
        return [RoomInfo(r, n) for r, n in rows], total


async def my_rooms(user_id: int) -> list[RoomInfo]:
    async with session_scope() as s:
        rooms = (
            await s.execute(select(Room).where(Room.owner_id == user_id, Room.is_active.is_(True)))
        ).scalars().all()
        return [RoomInfo(r, await online_count(s, r.id)) for r in rooms]


async def get_room(room_id: int) -> RoomInfo | None:
    async with session_scope() as s:
        room = await s.get(Room, room_id)
        if room is None:
            return None
        return RoomInfo(room, await online_count(s, room.id))


async def get_room_by_code(code: str) -> Room | None:
    async with session_scope() as s:
        return (await s.execute(select(Room).where(Room.invite_code == code))).scalars().first()


async def random_room_id(user: User) -> int | None:
    """Самая живая публичная комната, где есть место."""
    rooms, _ = await list_public_rooms(0, 50)
    for info in rooms:
        if info.room.vip_only and not user.is_vip:
            continue
        if info.online < info.room.max_members:
            return info.room.id
    return None


async def join_room(user_id: int, room_id: int) -> tuple[Room, RoomMember, list[int]]:
    """Входит в комнату. Возвращает (комната, участник, id остальных участников)."""
    async with session_scope() as s:
        user = await s.get(User, user_id)
        room = await s.get(Room, room_id)
        if user is None or room is None or not room.is_active:
            raise RoomError("Комната не найдена или закрыта.")
        if user.state == UserState.chatting:
            raise RoomError("Сначала заверши текущий диалог.")
        if room.vip_only and not user.is_vip:
            raise RoomError("Эта комната только для 👑 VIP.")
        kicked = (
            await s.execute(
                select(RoomMember.id).where(
                    RoomMember.room_id == room_id, RoomMember.user_id == user_id, RoomMember.kicked.is_(True)
                )
            )
        ).first()
        if kicked:
            raise RoomError("Тебя исключили из этой комнаты.")
        if user.current_room_id == room_id and user.state == UserState.in_room:
            member = await _active_member(s, room_id, user_id)
            if member:
                return room, member, await _member_ids(s, room_id, exclude=user_id)
        if await online_count(s, room_id) >= room.max_members:
            raise RoomError("В комнате нет свободных мест 😔")

        # выходим из предыдущей комнаты
        if user.current_room_id:
            prev = await _active_member(s, user.current_room_id, user_id)
            if prev:
                prev.left_at = utcnow()

        taken = set(
            (await s.execute(
                select(RoomMember.nickname).where(RoomMember.room_id == room_id, RoomMember.left_at.is_(None))
            )).scalars()
        )
        nick = random_nickname()
        for _ in range(20):
            if nick not in taken:
                break
            nick = random_nickname()
        member = RoomMember(room_id=room_id, user_id=user_id, nickname=nick)
        s.add(member)
        user.state = UserState.in_room
        user.current_room_id = room_id
        user.search_since = None
        await s.flush()
        return room, member, await _member_ids(s, room_id, exclude=user_id)


async def leave_room(user_id: int) -> tuple[Room, RoomMember, list[int]] | None:
    async with session_scope() as s:
        user = await s.get(User, user_id)
        if user is None or not user.current_room_id:
            return None
        room = await s.get(Room, user.current_room_id)
        member = await _active_member(s, user.current_room_id, user_id)
        if member:
            member.left_at = utcnow()
        user.current_room_id = None
        if user.state == UserState.in_room:
            user.state = UserState.idle
        if room is None or member is None:
            return None
        return room, member, await _member_ids(s, room.id, exclude=user_id)


async def kick_member(room_id: int, target_user_id: int) -> RoomMember | None:
    async with session_scope() as s:
        member = await _active_member(s, room_id, target_user_id)
        if member is None:
            return None
        member.left_at = utcnow()
        member.kicked = True
        user = await s.get(User, target_user_id)
        if user and user.current_room_id == room_id:
            user.current_room_id = None
            if user.state == UserState.in_room:
                user.state = UserState.idle
        return member


async def room_members(room_id: int) -> list[RoomMember]:
    async with session_scope() as s:
        return list(
            (await s.execute(
                select(RoomMember)
                .where(RoomMember.room_id == room_id, RoomMember.left_at.is_(None))
                .order_by(RoomMember.joined_at)
            )).scalars()
        )


async def member_of(room_id: int, user_id: int) -> RoomMember | None:
    async with session_scope() as s:
        return await _active_member(s, room_id, user_id)


async def set_nickname(room_id: int, user_id: int, nickname: str) -> str:
    async with session_scope() as s:
        member = await _active_member(s, room_id, user_id)
        if member is None:
            raise RoomError("Ты не в комнате.")
        taken = (
            await s.execute(
                select(RoomMember.id).where(
                    RoomMember.room_id == room_id,
                    RoomMember.left_at.is_(None),
                    func.lower(RoomMember.nickname) == nickname.lower(),
                    RoomMember.id != member.id,
                )
            )
        ).first()
        if taken:
            raise RoomError("Такой ник уже занят в этой комнате.")
        old = member.nickname
        member.nickname = nickname
        return old


async def create_room(
    owner: User, title: str, emoji: str, description: str | None, is_public: bool, max_members: int
) -> Room:
    async with session_scope() as s:
        room = Room(
            title=title,
            emoji=emoji,
            description=description,
            is_public=is_public,
            max_members=max_members,
            owner_id=owner.id,
            invite_code=invite_code(),
        )
        s.add(room)
        await s.flush()
        return room


async def close_room(room_id: int) -> list[int]:
    """Закрывает комнату, выкидывает участников. Возвращает их id."""
    async with session_scope() as s:
        room = await s.get(Room, room_id)
        if room is None:
            return []
        room.is_active = False
        members = (
            await s.execute(select(RoomMember).where(RoomMember.room_id == room_id, RoomMember.left_at.is_(None)))
        ).scalars().all()
        ids = []
        for m in members:
            m.left_at = utcnow()
            u = await s.get(User, m.user_id)
            if u and u.current_room_id == room_id:
                u.current_room_id = None
                if u.state == UserState.in_room:
                    u.state = UserState.idle
            ids.append(m.user_id)
        return ids


async def _active_member(s: AsyncSession, room_id: int, user_id: int) -> RoomMember | None:
    return (
        await s.execute(
            select(RoomMember).where(
                RoomMember.room_id == room_id, RoomMember.user_id == user_id, RoomMember.left_at.is_(None)
            )
        )
    ).scalars().first()


async def _member_ids(s: AsyncSession, room_id: int, exclude: int | None = None) -> list[int]:
    q = select(RoomMember.user_id).where(RoomMember.room_id == room_id, RoomMember.left_at.is_(None))
    if exclude is not None:
        q = q.where(RoomMember.user_id != exclude)
    return list((await s.execute(q)).scalars())
