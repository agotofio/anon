"""Подбор собеседников один на один.

Очередь хранится прямо в таблице users (state = searching), поэтому переживает
перезапуск бота. Все операции над очередью выполняются под одним asyncio.Lock —
этого достаточно для одного процесса бота (десятки тысяч онлайна).

Правила подбора:
  * не соединяем пользователя с самим собой, забаненными и с тем, кто поставил
    дизлайк (или кому поставили дизлайк) — «чёрный список» на основе оценок;
  * первые RELAX_AFTER секунд не соединяем с предыдущим собеседником;
  * VIP-фильтры (пол, возраст) учитываются В ОБЕ стороны: если VIP ищет девушек,
    то парень ему не попадётся, а если VIP-девушка ищет парней, то она не
    попадётся девушке;
  * VIP-пользователи в очереди идут первыми, внутри группы — по времени ожидания.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import timedelta

from sqlalchemy import and_, case, exists, not_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import session_scope
from ..models import Dialog, Rating, User, UserState, utcnow

RELAX_AFTER = timedelta(seconds=30)

_lock = asyncio.Lock()


@dataclass
class Match:
    dialog_id: int
    user_a: User
    user_b: User


def _vip_expr(now):
    return or_(User.vip_forever.is_(True), and_(User.vip_until.is_not(None), User.vip_until > now))


def _candidate_query(me: User, allow_last_partner: bool):
    now = utcnow()
    cand_vip = _vip_expr(now)
    q = select(User).where(
        User.state == UserState.searching,
        User.id != me.id,
        User.is_banned.is_(False),
        User.bot_blocked.is_(False),
    )

    if not allow_last_partner:
        if me.last_partner_id:
            q = q.where(User.id != me.last_partner_id)
        q = q.where(or_(User.last_partner_id.is_(None), User.last_partner_id != me.id))

    # Мои фильтры (действуют только у VIP)
    if me.is_vip:
        if me.pref_gender:
            q = q.where(User.gender == me.pref_gender)
        if me.pref_age_min:
            q = q.where(User.age >= me.pref_age_min)
        if me.pref_age_max:
            q = q.where(User.age <= me.pref_age_max)

    # Фильтры кандидата
    gender_ok = or_(User.pref_gender.is_(None), not_(cand_vip))
    if me.gender is not None:
        gender_ok = or_(gender_ok, User.pref_gender == me.gender)
    q = q.where(gender_ok)

    if me.age is not None:
        age_ok = and_(
            or_(User.pref_age_min.is_(None), User.pref_age_min <= me.age),
            or_(User.pref_age_max.is_(None), User.pref_age_max >= me.age),
        )
    else:
        age_ok = and_(User.pref_age_min.is_(None), User.pref_age_max.is_(None))
    q = q.where(or_(not_(cand_vip), age_ok))

    # Взаимный «чёрный список» по дизлайкам
    disliked = exists().where(
        Rating.value < 0,
        or_(
            and_(Rating.from_user_id == me.id, Rating.to_user_id == User.id),
            and_(Rating.from_user_id == User.id, Rating.to_user_id == me.id),
        ),
    )
    q = q.where(not_(disliked))

    return q.order_by(case((cand_vip, 0), else_=1), User.search_since.asc()).limit(1)


async def _try_match(s: AsyncSession, me: User) -> Match | None:
    allow_last = bool(me.search_since and utcnow() - me.search_since > RELAX_AFTER)
    partner = (await s.execute(_candidate_query(me, allow_last))).scalars().first()
    if partner is None:
        return None
    dialog = Dialog(user1_id=me.id, user2_id=partner.id)
    s.add(dialog)
    await s.flush()
    for u in (me, partner):
        u.state = UserState.chatting
        u.search_since = None
        u.current_dialog_id = dialog.id
        u.dialogs_count += 1
    return Match(dialog.id, me, partner)


async def start_search(user_id: int) -> Match | None:
    """Ставит пользователя в очередь и сразу пытается найти пару."""
    async with _lock:
        async with session_scope() as s:
            me = await s.get(User, user_id)
            if me is None or me.state in (UserState.chatting, UserState.in_room):
                return None
            if me.state != UserState.searching:
                me.state = UserState.searching
                me.search_since = utcnow()
            return await _try_match(s, me)


async def cancel_search(user_id: int) -> bool:
    async with _lock:
        async with session_scope() as s:
            me = await s.get(User, user_id)
            if me is None or me.state != UserState.searching:
                return False
            me.state = UserState.idle
            me.search_since = None
            return True


async def match_pending() -> list[Match]:
    """Фоновый проход по очереди: подбирает пары, в т.ч. после ослабления ограничений."""
    matches: list[Match] = []
    async with _lock:
        async with session_scope() as s:
            waiting = (
                await s.execute(
                    select(User)
                    .where(User.state == UserState.searching)
                    .order_by(case((_vip_expr(utcnow()), 0), else_=1), User.search_since.asc())
                )
            ).scalars().all()
            for me in waiting:
                if me.state != UserState.searching:
                    continue  # уже сматчился в этом проходе
                m = await _try_match(s, me)
                if m:
                    matches.append(m)
    return matches


async def end_dialog(user_id: int) -> tuple[Dialog, int] | None:
    """Завершает текущий диалог пользователя. Возвращает (диалог, id собеседника)."""
    async with _lock:
        async with session_scope() as s:
            me = await s.get(User, user_id)
            if me is None or me.state != UserState.chatting or not me.current_dialog_id:
                return None
            dialog = await s.get(Dialog, me.current_dialog_id)
            if dialog is None:
                me.state = UserState.idle
                me.current_dialog_id = None
                return None
            partner = await s.get(User, dialog.partner_of(me.id))
            dialog.ended_at = utcnow()
            dialog.ended_by = me.id
            for u, other in ((me, partner), (partner, me)):
                if u is None:
                    continue
                if u.current_dialog_id == dialog.id:
                    u.state = UserState.idle
                    u.current_dialog_id = None
                u.last_partner_id = other.id if other else None
            return dialog, dialog.partner_of(me.id)


async def connect_pair(user_a_id: int, user_b_id: int) -> Match | None:
    """Прямое соединение (VIP «вернуть собеседника»), если оба свободны."""
    async with _lock:
        async with session_scope() as s:
            a = await s.get(User, user_a_id)
            b = await s.get(User, user_b_id)
            if not a or not b or a.is_banned or b.is_banned:
                return None
            if a.state not in (UserState.idle, UserState.searching):
                return None
            if b.state not in (UserState.idle, UserState.searching):
                return None
            dialog = Dialog(user1_id=a.id, user2_id=b.id)
            s.add(dialog)
            await s.flush()
            for u in (a, b):
                u.state = UserState.chatting
                u.search_since = None
                u.current_dialog_id = dialog.id
                u.dialogs_count += 1
            return Match(dialog.id, a, b)


async def queue_size() -> int:
    from sqlalchemy import func

    async with session_scope() as s:
        return (
            await s.execute(select(func.count()).select_from(User).where(User.state == UserState.searching))
        ).scalar_one()
