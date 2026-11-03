from __future__ import annotations

from datetime import timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from ..config import VIP_FOREVER, get_settings
from ..db import session_scope
from ..models import User, utcnow


async def get_or_create_user(
    session: AsyncSession,
    tg_user,
    referrer_id: int | None = None,
) -> tuple[User, bool]:
    user = await session.get(User, tg_user.id)
    created = False
    if user is None:
        user = User(
            id=tg_user.id,
            username=tg_user.username,
            first_name=tg_user.first_name,
            language=tg_user.language_code,
        )
        if referrer_id and referrer_id != tg_user.id:
            ref = await session.get(User, referrer_id)
            if ref is not None:
                user.referrer_id = ref.id
                ref.referrals_count += 1
                bonus = get_settings().referral_bonus_days
                if bonus:
                    extend_vip(ref, bonus)
        session.add(user)
        created = True
    else:
        user.username = tg_user.username
        user.first_name = tg_user.first_name
        user.bot_blocked = False
    user.last_active = utcnow()
    # срок бана истёк
    if user.is_banned and user.ban_until and user.ban_until <= utcnow():
        user.is_banned = False
        user.ban_until = None
        user.ban_reason = None
    return user, created


def extend_vip(user: User, days: int) -> None:
    if days == VIP_FOREVER:
        user.vip_forever = True
        return
    base = user.vip_until if user.vip_until and user.vip_until > utcnow() else utcnow()
    user.vip_until = base + timedelta(days=days)


async def grant_vip(user_id: int, days: int) -> User | None:
    async with session_scope() as s:
        user = await s.get(User, user_id)
        if user is None:
            return None
        extend_vip(user, days)
        return user


async def revoke_vip(user_id: int) -> None:
    async with session_scope() as s:
        user = await s.get(User, user_id)
        if user:
            user.vip_forever = False
            user.vip_until = None


async def ban_user(user_id: int, reason: str = "", hours: int | None = None) -> User | None:
    async with session_scope() as s:
        user = await s.get(User, user_id)
        if user is None:
            return None
        user.is_banned = True
        user.ban_reason = reason or "Нарушение правил"
        user.ban_until = utcnow() + timedelta(hours=hours) if hours else None
        return user


async def unban_user(user_id: int) -> None:
    async with session_scope() as s:
        user = await s.get(User, user_id)
        if user:
            user.is_banned = False
            user.ban_reason = None
            user.ban_until = None
