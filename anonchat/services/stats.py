from __future__ import annotations

from datetime import datetime, timedelta

from sqlalchemy import and_, func, or_, select

from ..db import session_scope
from ..models import Dialog, Message, Payment, Report, ReportStatus, Room, RoomMember, User, UserState, utcnow


async def overview() -> dict:
    now = utcnow()
    day = now - timedelta(days=1)
    week = now - timedelta(days=7)
    month = now - timedelta(days=30)
    today = now.replace(hour=0, minute=0, second=0, microsecond=0)

    async def scalar(q):
        return (await s.execute(q)).scalar_one() or 0

    async with session_scope() as s:
        cnt = select(func.count())
        vip_expr = or_(User.vip_forever.is_(True), and_(User.vip_until.is_not(None), User.vip_until > now))
        data = {
            "users_total": await scalar(cnt.select_from(User)),
            "users_today": await scalar(cnt.select_from(User).where(User.created_at >= today)),
            "users_week": await scalar(cnt.select_from(User).where(User.created_at >= week)),
            "dau": await scalar(cnt.select_from(User).where(User.last_active >= day)),
            "wau": await scalar(cnt.select_from(User).where(User.last_active >= week)),
            "mau": await scalar(cnt.select_from(User).where(User.last_active >= month)),
            "searching": await scalar(cnt.select_from(User).where(User.state == UserState.searching)),
            "chatting": await scalar(cnt.select_from(User).where(User.state == UserState.chatting)),
            "in_rooms": await scalar(cnt.select_from(RoomMember).where(RoomMember.left_at.is_(None))),
            "active_dialogs": await scalar(cnt.select_from(Dialog).where(Dialog.ended_at.is_(None))),
            "dialogs_total": await scalar(cnt.select_from(Dialog)),
            "dialogs_today": await scalar(cnt.select_from(Dialog).where(Dialog.started_at >= today)),
            "messages_total": await scalar(cnt.select_from(Message)),
            "messages_today": await scalar(cnt.select_from(Message).where(Message.created_at >= today)),
            "media_total": await scalar(cnt.select_from(Message).where(Message.file_id.is_not(None))),
            "rooms_active": await scalar(cnt.select_from(Room).where(Room.is_active.is_(True))),
            "vip_now": await scalar(cnt.select_from(User).where(vip_expr)),
            "banned": await scalar(cnt.select_from(User).where(User.is_banned.is_(True))),
            "reports_open": await scalar(cnt.select_from(Report).where(Report.status == ReportStatus.open)),
            "stars_total": await scalar(
                select(func.coalesce(func.sum(Payment.stars), 0)).where(Payment.refunded.is_(False))
            ),
            "stars_month": await scalar(
                select(func.coalesce(func.sum(Payment.stars), 0)).where(
                    Payment.refunded.is_(False), Payment.created_at >= month
                )
            ),
            "stars_today": await scalar(
                select(func.coalesce(func.sum(Payment.stars), 0)).where(
                    Payment.refunded.is_(False), Payment.created_at >= today
                )
            ),
            "payments_total": await scalar(cnt.select_from(Payment).where(Payment.refunded.is_(False))),
            "avg_dialog_messages": round(
                float(await scalar(select(func.avg(Dialog.messages_count)).where(Dialog.ended_at.is_not(None)))), 1
            ),
            "male": await scalar(cnt.select_from(User).where(User.gender == "male")),
            "female": await scalar(cnt.select_from(User).where(User.gender == "female")),
        }
    data["online"] = data["searching"] + data["chatting"] + data["in_rooms"]
    return data


async def daily_series(days: int = 30) -> dict:
    """Ряды по дням для графиков: регистрации, сообщения, диалоги, звёзды."""
    start = (utcnow() - timedelta(days=days - 1)).replace(hour=0, minute=0, second=0, microsecond=0)
    labels = [(start + timedelta(days=i)).strftime("%Y-%m-%d") for i in range(days)]

    async def series(col, value=None, extra=()):
        day = func.strftime("%Y-%m-%d", col) if _is_sqlite() else func.to_char(col, "YYYY-MM-DD")
        agg = func.count() if value is None else func.coalesce(func.sum(value), 0)
        q = select(day, agg).where(col >= start, *extra).group_by(day)
        rows = dict((await s.execute(q)).all())
        return [int(rows.get(d, 0)) for d in labels]

    async with session_scope() as s:
        return {
            "labels": [d[5:] for d in labels],
            "users": await series(User.created_at),
            "messages": await series(Message.created_at),
            "dialogs": await series(Dialog.started_at),
            "stars": await series(Payment.created_at, Payment.stars, (Payment.refunded.is_(False),)),
        }


async def hourly_activity() -> list[int]:
    """Сообщения по часам суток за последние 7 дней."""
    since = utcnow() - timedelta(days=7)
    async with session_scope() as s:
        hour = func.strftime("%H", Message.created_at) if _is_sqlite() else func.to_char(Message.created_at, "HH24")
        rows = dict((await s.execute(select(hour, func.count()).where(Message.created_at >= since).group_by(hour))).all())
    return [int(rows.get(f"{h:02d}", 0)) for h in range(24)]


async def content_types() -> dict[str, int]:
    async with session_scope() as s:
        rows = (await s.execute(select(Message.content_type, func.count()).group_by(Message.content_type))).all()
    return {k: int(v) for k, v in sorted(rows, key=lambda r: -r[1])}


def _is_sqlite() -> bool:
    from ..config import get_settings

    return get_settings().database_url.startswith("sqlite")


def as_naive(dt: datetime) -> datetime:
    return dt.replace(tzinfo=None) if dt.tzinfo else dt
