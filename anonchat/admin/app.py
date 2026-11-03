"""Веб-админка: статистика, просмотр всех диалогов/комнат/медиа, модерация, платежи, рассылки."""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import mimetypes
import os
import secrets
import time
from collections import defaultdict
from pathlib import Path

from fastapi import Depends, FastAPI, Form, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy import and_, func, or_, select
from starlette.middleware.sessions import SessionMiddleware

from ..config import PRODUCTS, get_settings
from ..db import session_scope
from ..models import (
    Broadcast, Dialog, Message, Payment, Report, ReportStatus, Room, RoomMember, User, UserState, utcnow,
)
from ..services import rooms as rooms_svc
from ..services import stats
from ..services.users import ban_user, grant_vip, revoke_vip, unban_user
from ..utils import fmt_dt, gender_label, rating_text, vip_status_text

log = logging.getLogger(__name__)
settings = get_settings()
BASE = Path(__file__).parent

app = FastAPI(title="AnonChat Admin", docs_url=None, redoc_url=None, openapi_url=None)
app.add_middleware(
    SessionMiddleware, secret_key=settings.secret_key, session_cookie="anon_admin",
    max_age=60 * 60 * 24 * 7, same_site="strict", https_only=False,
)
app.mount("/static", StaticFiles(directory=BASE / "static"), name="static")
templates = Jinja2Templates(directory=BASE / "templates")
templates.env.globals.update(
    fmt_dt=fmt_dt, gender_label=gender_label, rating_text=rating_text, vip_status_text=vip_status_text,
    PRODUCTS=PRODUCTS,
)


def nick_color(value) -> str:
    h = int(hashlib.md5(str(value).encode()).hexdigest()[:6], 16)
    return f"hsl({h % 360} 70% 60%)"


templates.env.filters["nick_color"] = nick_color
templates.env.filters["tojson_safe"] = lambda v: json.dumps(v)

PER_PAGE = 50


# ---------------------------------------------------------------- auth
class NeedLogin(Exception):
    pass


@app.exception_handler(NeedLogin)
async def _need_login(request: Request, exc: NeedLogin):
    return RedirectResponse(f"/login?next={request.url.path}", status_code=303)


def require_admin(request: Request) -> str:
    if not request.session.get("admin"):
        raise NeedLogin()
    return request.session["admin"]


_login_attempts: dict[str, list[float]] = defaultdict(list)


@app.get("/login", response_class=HTMLResponse)
async def login_page(request: Request, next: str = "/"):
    return templates.TemplateResponse(request, "login.html", {"next": next, "error": None})


@app.post("/login")
async def login(request: Request, login: str = Form(...), password: str = Form(...), next: str = Form("/")):
    ip = request.client.host if request.client else "?"
    now = time.time()
    attempts = [t for t in _login_attempts[ip] if now - t < 600]
    _login_attempts[ip] = attempts
    if len(attempts) >= 10:
        return templates.TemplateResponse(
            request, "login.html", {"next": next, "error": "Слишком много попыток. Подождите 10 минут."}, status_code=429
        )
    ok = secrets.compare_digest(login, settings.admin_login) and secrets.compare_digest(password, settings.admin_password)
    if not ok:
        attempts.append(now)
        await asyncio.sleep(1)
        return templates.TemplateResponse(
            request, "login.html", {"next": next, "error": "Неверный логин или пароль"}, status_code=401
        )
    request.session["admin"] = login
    if not next.startswith("/") or next.startswith("//"):
        next = "/"
    return RedirectResponse(next, status_code=303)


@app.get("/logout")
async def logout(request: Request):
    request.session.clear()
    return RedirectResponse("/login", status_code=303)


def render(request: Request, name: str, ctx: dict | None = None, **kw):
    ctx = dict(ctx or {})
    ctx.setdefault("active", name.split(".")[0])
    return templates.TemplateResponse(request, name, ctx, **kw)


def pages(total: int, page: int) -> dict:
    last = max(1, (total + PER_PAGE - 1) // PER_PAGE)
    return {"page": page, "last": last, "total": total}


# ---------------------------------------------------------------- dashboard
@app.get("/", response_class=HTMLResponse)
async def dashboard(request: Request, _=Depends(require_admin)):
    data = await stats.overview()
    series = await stats.daily_series(30)
    hourly = await stats.hourly_activity()
    types = await stats.content_types()
    async with session_scope() as s:
        top_rooms = (
            await s.execute(select(Room).where(Room.is_active.is_(True)).order_by(Room.messages_count.desc()).limit(6))
        ).scalars().all()
        recent_payments = (await s.execute(select(Payment).order_by(Payment.id.desc()).limit(6))).scalars().all()
        recent_reports = (
            await s.execute(select(Report).where(Report.status == ReportStatus.open).order_by(Report.id.desc()).limit(6))
        ).scalars().all()
    return render(request, "dashboard.html", {
        "d": data, "series": series, "hourly": hourly, "types": types,
        "top_rooms": top_rooms, "recent_payments": recent_payments, "recent_reports": recent_reports,
    })


@app.get("/api/overview")
async def api_overview(_=Depends(require_admin)):
    return await stats.overview()


# ---------------------------------------------------------------- live
@app.get("/live", response_class=HTMLResponse)
async def live(request: Request, _=Depends(require_admin)):
    async with session_scope() as s:
        dialogs = (
            await s.execute(select(Dialog).where(Dialog.ended_at.is_(None)).order_by(Dialog.started_at.desc()).limit(200))
        ).scalars().all()
        searching = (
            await s.execute(select(User).where(User.state == UserState.searching).order_by(User.search_since))
        ).scalars().all()
        online_sq = (
            select(RoomMember.room_id, func.count().label("n")).where(RoomMember.left_at.is_(None))
            .group_by(RoomMember.room_id).subquery()
        )
        room_rows = (
            await s.execute(
                select(Room, online_sq.c.n).join(online_sq, online_sq.c.room_id == Room.id).order_by(online_sq.c.n.desc())
            )
        ).all()
    return render(request, "live.html", {"dialogs": dialogs, "searching": searching, "room_rows": room_rows, "now": utcnow()})


# ---------------------------------------------------------------- dialogs
@app.get("/dialogs", response_class=HTMLResponse)
async def dialogs_list(request: Request, status: str = "all", user: int | None = None, page: int = 1,
                       _=Depends(require_admin)):
    async with session_scope() as s:
        q = select(Dialog)
        cq = select(func.count()).select_from(Dialog)
        conds = []
        if status == "active":
            conds.append(Dialog.ended_at.is_(None))
        elif status == "ended":
            conds.append(Dialog.ended_at.is_not(None))
        if user:
            conds.append(or_(Dialog.user1_id == user, Dialog.user2_id == user))
        if conds:
            q, cq = q.where(*conds), cq.where(*conds)
        total = (await s.execute(cq)).scalar_one()
        rows = (
            await s.execute(q.order_by(Dialog.id.desc()).offset((page - 1) * PER_PAGE).limit(PER_PAGE))
        ).scalars().all()
    return render(request, "dialogs.html", {"dialogs": rows, "status": status, "user": user, **pages(total, page)})


async def _messages(cond, after: int = 0, limit: int = 500) -> list[Message]:
    async with session_scope() as s:
        q = select(Message).where(cond, Message.id > after).order_by(Message.id.desc()).limit(limit)
        rows = list((await s.execute(q)).scalars().all())
    rows.reverse()
    return rows


@app.get("/dialogs/{dialog_id}", response_class=HTMLResponse)
async def dialog_view(request: Request, dialog_id: int, _=Depends(require_admin)):
    async with session_scope() as s:
        dialog = await s.get(Dialog, dialog_id)
        if dialog is None:
            raise HTTPException(404)
        reports = (await s.execute(select(Report).where(Report.dialog_id == dialog_id))).scalars().all()
    msgs = await _messages(Message.dialog_id == dialog_id, limit=5000)
    return render(request, "chat.html", {
        "kind": "dialog", "dialog": dialog, "messages": msgs, "reports": reports, "active": "dialogs",
        "left_id": dialog.user1_id, "poll_url": f"/dialogs/{dialog_id}/messages",
    })


@app.get("/dialogs/{dialog_id}/messages", response_class=HTMLResponse)
async def dialog_poll(request: Request, dialog_id: int, after: int = 0, _=Depends(require_admin)):
    async with session_scope() as s:
        dialog = await s.get(Dialog, dialog_id)
    msgs = await _messages(Message.dialog_id == dialog_id, after)
    return render(request, "_messages.html", {"messages": msgs, "kind": "dialog", "left_id": dialog.user1_id})


@app.post("/dialogs/{dialog_id}/end")
async def dialog_end(dialog_id: int, _=Depends(require_admin)):
    from ..bot.common import finish_dialog
    from ..bot.setup import get_bot

    async with session_scope() as s:
        dialog = await s.get(Dialog, dialog_id)
        u = await s.get(User, dialog.user1_id) if dialog else None
    if u and dialog.ended_at is None:
        from ..bot.common import safe_send
        from ..bot.keyboards import main_menu

        await finish_dialog(get_bot(), u, silent_me=True)
        await safe_send(get_bot(), u.id, "⛔ Диалог завершён модерацией.", reply_markup=main_menu())
    return RedirectResponse(f"/dialogs/{dialog_id}", status_code=303)


# ---------------------------------------------------------------- rooms
@app.get("/rooms", response_class=HTMLResponse)
async def rooms_list(request: Request, _=Depends(require_admin)):
    async with session_scope() as s:
        online_sq = (
            select(RoomMember.room_id, func.count().label("n")).where(RoomMember.left_at.is_(None))
            .group_by(RoomMember.room_id).subquery()
        )
        rows = (
            await s.execute(
                select(Room, func.coalesce(online_sq.c.n, 0)).outerjoin(online_sq, online_sq.c.room_id == Room.id)
                .order_by(Room.is_active.desc(), Room.sort_order, Room.id)
            )
        ).all()
    return render(request, "rooms.html", {"rows": rows})


@app.post("/rooms/new")
async def room_new(title: str = Form(...), emoji: str = Form("💬"), description: str = Form(""),
                   max_members: int = Form(100), vip_only: bool = Form(False), is_public: bool = Form(False),
                   _=Depends(require_admin)):
    from ..utils import invite_code

    async with session_scope() as s:
        s.add(Room(title=title[:64], emoji=emoji[:8] or "💬", description=description[:255] or None,
                   max_members=max(2, min(max_members, 1000)), vip_only=vip_only, is_public=is_public,
                   invite_code=invite_code()))
    return RedirectResponse("/rooms", status_code=303)


@app.post("/rooms/{room_id}/update")
async def room_update(room_id: int, title: str = Form(...), emoji: str = Form("💬"), description: str = Form(""),
                      max_members: int = Form(100), sort_order: int = Form(100), vip_only: bool = Form(False),
                      is_public: bool = Form(False), _=Depends(require_admin)):
    async with session_scope() as s:
        room = await s.get(Room, room_id)
        if room is None:
            raise HTTPException(404)
        room.title, room.emoji = title[:64], emoji[:8] or "💬"
        room.description = description[:255] or None
        room.max_members = max(2, min(max_members, 1000))
        room.sort_order, room.vip_only, room.is_public = sort_order, vip_only, is_public
    return RedirectResponse(f"/rooms/{room_id}", status_code=303)


@app.post("/rooms/{room_id}/close")
async def room_close(room_id: int, _=Depends(require_admin)):
    from ..bot.common import safe_send
    from ..bot.keyboards import main_menu
    from ..bot.setup import get_bot

    async with session_scope() as s:
        room = await s.get(Room, room_id)
    for uid in await rooms_svc.close_room(room_id):
        await safe_send(get_bot(), uid, f"🗑 Комната {room.emoji} {room.title} закрыта администрацией.",
                        reply_markup=main_menu())
    return RedirectResponse(f"/rooms/{room_id}", status_code=303)


@app.post("/rooms/{room_id}/reopen")
async def room_reopen(room_id: int, _=Depends(require_admin)):
    async with session_scope() as s:
        room = await s.get(Room, room_id)
        room.is_active = True
    return RedirectResponse(f"/rooms/{room_id}", status_code=303)


@app.get("/rooms/{room_id}", response_class=HTMLResponse)
async def room_view(request: Request, room_id: int, _=Depends(require_admin)):
    async with session_scope() as s:
        room = await s.get(Room, room_id)
        if room is None:
            raise HTTPException(404)
        members = (
            await s.execute(
                select(RoomMember, User).join(User, User.id == RoomMember.user_id)
                .where(RoomMember.room_id == room_id, RoomMember.left_at.is_(None))
            )
        ).all()
        owner = await s.get(User, room.owner_id) if room.owner_id else None
    msgs = await _messages(Message.room_id == room_id, limit=3000)
    return render(request, "chat.html", {
        "kind": "room", "room": room, "members": members, "owner": owner, "messages": msgs, "active": "rooms",
        "left_id": None, "poll_url": f"/rooms/{room_id}/messages", "bot_username": await _bot_username(),
    })


@app.get("/rooms/{room_id}/messages", response_class=HTMLResponse)
async def room_poll(request: Request, room_id: int, after: int = 0, _=Depends(require_admin)):
    msgs = await _messages(Message.room_id == room_id, after)
    return render(request, "_messages.html", {"messages": msgs, "kind": "room", "left_id": None})


# ---------------------------------------------------------------- users
@app.get("/users", response_class=HTMLResponse)
async def users_list(request: Request, q: str = "", filter: str = "all", page: int = 1, _=Depends(require_admin)):
    now = utcnow()
    conds = []
    if q:
        like = f"%{q.lstrip('@')}%"
        id_cond = [User.id == int(q)] if q.isdigit() else []
        conds.append(or_(User.username.ilike(like), User.first_name.ilike(like), *id_cond))
    if filter == "vip":
        conds.append(or_(User.vip_forever.is_(True), User.vip_until > now))
    elif filter == "banned":
        conds.append(User.is_banned.is_(True))
    elif filter == "online":
        conds.append(User.state != UserState.idle)
    elif filter == "paying":
        conds.append(User.stars_spent > 0)
    elif filter == "reported":
        conds.append(User.id.in_(select(Report.reported_id).where(Report.status == ReportStatus.open)))
    async with session_scope() as s:
        total = (await s.execute(select(func.count()).select_from(User).where(*conds))).scalar_one()
        rows = (
            await s.execute(
                select(User).where(*conds).order_by(User.last_active.desc())
                .offset((page - 1) * PER_PAGE).limit(PER_PAGE)
            )
        ).scalars().all()
    return render(request, "users.html", {"users": rows, "q": q, "filter": filter, **pages(total, page)})


@app.get("/users/{user_id}", response_class=HTMLResponse)
async def user_view(request: Request, user_id: int, _=Depends(require_admin)):
    async with session_scope() as s:
        user = await s.get(User, user_id)
        if user is None:
            raise HTTPException(404)
        dialogs = (
            await s.execute(
                select(Dialog).where(or_(Dialog.user1_id == user_id, Dialog.user2_id == user_id))
                .order_by(Dialog.id.desc()).limit(100)
            )
        ).scalars().all()
        memberships = (
            await s.execute(
                select(RoomMember, Room).join(Room, Room.id == RoomMember.room_id)
                .where(RoomMember.user_id == user_id).order_by(RoomMember.id.desc()).limit(50)
            )
        ).all()
        payments = (await s.execute(select(Payment).where(Payment.user_id == user_id).order_by(Payment.id.desc()))).scalars().all()
        reports_on = (await s.execute(select(Report).where(Report.reported_id == user_id).order_by(Report.id.desc()))).scalars().all()
        reports_by = (await s.execute(select(Report).where(Report.reporter_id == user_id).order_by(Report.id.desc()))).scalars().all()
        media = (
            await s.execute(
                select(Message).where(Message.sender_id == user_id, Message.file_id.is_not(None))
                .order_by(Message.id.desc()).limit(48)
            )
        ).scalars().all()
        referrer = await s.get(User, user.referrer_id) if user.referrer_id else None
    return render(request, "user.html", {
        "u": user, "dialogs": dialogs, "memberships": memberships, "payments": payments,
        "reports_on": reports_on, "reports_by": reports_by, "user_media": media, "referrer": referrer, "active": "users",
    })


@app.post("/users/{user_id}/ban")
async def user_ban(user_id: int, reason: str = Form("Нарушение правил"), hours: int = Form(0), _=Depends(require_admin)):
    from ..bot.common import safe_send
    from ..bot.handlers.admin import kick_everywhere
    from ..bot.setup import get_bot

    await kick_everywhere(get_bot(), user_id)
    await ban_user(user_id, reason, hours or None)
    await safe_send(get_bot(), user_id, f"🚫 Ты заблокирован(а). Причина: {reason}")
    return RedirectResponse(f"/users/{user_id}", status_code=303)


@app.post("/users/{user_id}/unban")
async def user_unban(user_id: int, _=Depends(require_admin)):
    await unban_user(user_id)
    return RedirectResponse(f"/users/{user_id}", status_code=303)


@app.post("/users/{user_id}/vip")
async def user_vip(user_id: int, days: int = Form(...), _=Depends(require_admin)):
    from ..bot.common import safe_send
    from ..bot.setup import get_bot

    if days == 0:
        await revoke_vip(user_id)
    else:
        await grant_vip(user_id, days)
        await safe_send(get_bot(), user_id, "🎁 Администрация подарила тебе 👑 VIP!")
    return RedirectResponse(f"/users/{user_id}", status_code=303)


@app.post("/users/{user_id}/message")
async def user_message(user_id: int, text: str = Form(...), _=Depends(require_admin)):
    from ..bot.common import safe_send
    from ..bot.setup import get_bot

    await safe_send(get_bot(), user_id, f"📢 <b>Сообщение от администрации</b>\n\n{text}")
    return RedirectResponse(f"/users/{user_id}", status_code=303)


# ---------------------------------------------------------------- messages / media
@app.get("/messages", response_class=HTMLResponse)
async def messages_search(request: Request, q: str = "", type: str = "", page: int = 1, _=Depends(require_admin)):
    conds = []
    if q:
        conds.append(Message.text.ilike(f"%{q}%"))
    if type:
        conds.append(Message.content_type == type)
    async with session_scope() as s:
        total = (await s.execute(select(func.count()).select_from(Message).where(*conds))).scalar_one()
        rows = (
            await s.execute(
                select(Message).where(*conds).order_by(Message.id.desc()).offset((page - 1) * PER_PAGE).limit(PER_PAGE)
            )
        ).scalars().all()
    types = await stats.content_types()
    return render(request, "messages.html", {"messages": rows, "q": q, "type": type, "types": types, **pages(total, page)})


MEDIA_FILTERS = {
    "": None, "photo": ["photo"], "video": ["video", "video_note", "animation"],
    "voice": ["voice", "audio"], "sticker": ["sticker"], "document": ["document"],
}


@app.get("/gallery", response_class=HTMLResponse)
async def gallery(request: Request, type: str = "", page: int = 1, _=Depends(require_admin)):
    conds = [Message.file_id.is_not(None)]
    if MEDIA_FILTERS.get(type):
        conds.append(Message.content_type.in_(MEDIA_FILTERS[type]))
    per = 60
    async with session_scope() as s:
        total = (await s.execute(select(func.count()).select_from(Message).where(*conds))).scalar_one()
        rows = (
            await s.execute(select(Message).where(*conds).order_by(Message.id.desc()).offset((page - 1) * per).limit(per))
        ).scalars().all()
    last = max(1, (total + per - 1) // per)
    return render(request, "gallery.html", {"items": rows, "type": type, "page": page, "last": last, "total": total})


_download_locks: dict[str, asyncio.Lock] = defaultdict(asyncio.Lock)


@app.get("/file/{message_id}")
async def media_file(message_id: int, download: bool = False, _=Depends(require_admin)):
    """Отдаёт файл сообщения. Скачивается из Telegram при первом обращении и кэшируется на диске."""
    from aiogram.exceptions import TelegramBadRequest

    from ..bot.setup import get_bot

    async with session_scope() as s:
        msg = await s.get(Message, message_id)
    if msg is None or not msg.file_id:
        raise HTTPException(404)
    cache_dir = Path(settings.media_cache_dir)
    cache_dir.mkdir(parents=True, exist_ok=True)
    ext = mimetypes.guess_extension(msg.mime_type or "") or ""
    if msg.mime_type == "application/x-tgsticker":
        ext = ".tgs"
    path = cache_dir / f"{msg.file_unique_id or msg.id}{ext}"
    async with _download_locks[str(path)]:
        if not path.exists():
            try:
                bot = get_bot()
                f = await bot.get_file(msg.file_id)
                tmp = path.with_suffix(path.suffix + ".part")
                await bot.download_file(f.file_path, destination=tmp)
                os.replace(tmp, path)
            except TelegramBadRequest as e:
                # Bot API не отдаёт файлы > 20 МБ
                return Response(f"Файл недоступен через Bot API: {e.message}", status_code=502, media_type="text/plain")
    filename = msg.file_name or path.name
    return FileResponse(
        path, media_type=msg.mime_type or "application/octet-stream",
        filename=filename if download else None,
        content_disposition_type="attachment" if download else "inline",
    )


# ---------------------------------------------------------------- reports
@app.get("/reports", response_class=HTMLResponse)
async def reports_list(request: Request, status: str = "open", page: int = 1, _=Depends(require_admin)):
    conds = [Report.status == ReportStatus(status)] if status in ReportStatus.__members__ else []
    async with session_scope() as s:
        total = (await s.execute(select(func.count()).select_from(Report).where(*conds))).scalar_one()
        rows = (
            await s.execute(
                select(Report).where(*conds).order_by(Report.id.desc()).offset((page - 1) * PER_PAGE).limit(PER_PAGE)
            )
        ).scalars().all()
        counts = dict(
            (await s.execute(select(Report.reported_id, func.count()).group_by(Report.reported_id))).all()
        )
    from ..bot.texts import REPORT_REASONS

    return render(request, "reports.html", {
        "reports": rows, "status": status, "reasons": REPORT_REASONS, "counts": counts, **pages(total, page),
    })


@app.post("/reports/{report_id}/{action}")
async def report_action(report_id: int, action: str, hours: int = Form(0), _=Depends(require_admin)):
    from ..bot.common import safe_send
    from ..bot.handlers.admin import kick_everywhere
    from ..bot.setup import get_bot
    from ..bot.texts import REPORT_REASONS

    async with session_scope() as s:
        report = await s.get(Report, report_id)
        if report is None:
            raise HTTPException(404)
        if action == "reject":
            report.status = ReportStatus.rejected
        elif action in ("resolve", "ban"):
            report.status = ReportStatus.resolved
        reported_id, reason = report.reported_id, report.reason
    if action == "ban":
        label = REPORT_REASONS.get(reason, reason)
        await kick_everywhere(get_bot(), reported_id)
        await ban_user(reported_id, label, hours or None)
        await safe_send(get_bot(), reported_id, f"🚫 Ты заблокирован(а) по жалобе. Причина: {label}")
        async with session_scope() as s:  # закрываем остальные жалобы на него
            for r in (await s.execute(
                select(Report).where(Report.reported_id == reported_id, Report.status == ReportStatus.open)
            )).scalars():
                r.status = ReportStatus.resolved
    return RedirectResponse("/reports", status_code=303)


# ---------------------------------------------------------------- payments
@app.get("/payments", response_class=HTMLResponse)
async def payments_list(request: Request, page: int = 1, _=Depends(require_admin)):
    async with session_scope() as s:
        total = (await s.execute(select(func.count()).select_from(Payment))).scalar_one()
        rows = (
            await s.execute(select(Payment).order_by(Payment.id.desc()).offset((page - 1) * PER_PAGE).limit(PER_PAGE))
        ).scalars().all()
        by_product = (
            await s.execute(
                select(Payment.product, func.count(), func.sum(Payment.stars))
                .where(Payment.refunded.is_(False)).group_by(Payment.product).order_by(func.sum(Payment.stars).desc())
            )
        ).all()
        top = (
            await s.execute(select(User).where(User.stars_spent > 0).order_by(User.stars_spent.desc()).limit(10))
        ).scalars().all()
    d = await stats.overview()
    return render(request, "payments.html", {"payments": rows, "by_product": by_product, "top": top, "d": d,
                                             **pages(total, page)})


@app.post("/payments/{payment_id}/refund")
async def payment_refund(payment_id: int, _=Depends(require_admin)):
    from ..bot.setup import get_bot

    async with session_scope() as s:
        p = await s.get(Payment, payment_id)
        if p is None or p.refunded:
            raise HTTPException(404)
        await get_bot().refund_star_payment(user_id=p.user_id, telegram_payment_charge_id=p.charge_id)
        p.refunded = True
        user = await s.get(User, p.user_id)
        user.stars_spent = max(0, user.stars_spent - p.stars)
        if p.vip_days == -1:
            user.vip_forever = False
        elif p.vip_days and user.vip_until:
            from datetime import timedelta

            user.vip_until = user.vip_until - timedelta(days=p.vip_days)
    return RedirectResponse("/payments", status_code=303)


# ---------------------------------------------------------------- broadcast
@app.get("/broadcast", response_class=HTMLResponse)
async def broadcast_page(request: Request, _=Depends(require_admin)):
    async with session_scope() as s:
        items = (await s.execute(select(Broadcast).order_by(Broadcast.id.desc()).limit(20))).scalars().all()
    return render(request, "broadcast.html", {"items": items})


async def _run_broadcast(bid: int, text: str, target: str) -> None:
    from aiogram.exceptions import TelegramForbiddenError, TelegramRetryAfter

    from ..bot.setup import get_bot

    bot = get_bot()
    now = utcnow()
    conds = [User.bot_blocked.is_(False), User.is_banned.is_(False)]
    if target == "vip":
        conds.append(or_(User.vip_forever.is_(True), User.vip_until > now))
    elif target == "free":
        conds.append(and_(User.vip_forever.is_(False), or_(User.vip_until.is_(None), User.vip_until <= now)))
    elif target == "idle":
        conds.append(User.state == UserState.idle)
    async with session_scope() as s:
        ids = list((await s.execute(select(User.id).where(*conds))).scalars())
    sent = failed = 0
    for i, uid in enumerate(ids):
        try:
            await bot.send_message(uid, text)
            sent += 1
        except TelegramRetryAfter as e:
            await asyncio.sleep(e.retry_after)
            try:
                await bot.send_message(uid, text)
                sent += 1
            except Exception:
                failed += 1
        except TelegramForbiddenError:
            failed += 1
            async with session_scope() as s:
                u = await s.get(User, uid)
                if u:
                    u.bot_blocked = True
        except Exception:
            failed += 1
        await asyncio.sleep(0.04)  # ~25 сообщений/сек — в пределах лимитов Telegram
        if i % 50 == 0:
            async with session_scope() as s:
                b = await s.get(Broadcast, bid)
                b.sent, b.failed = sent, failed
    async with session_scope() as s:
        b = await s.get(Broadcast, bid)
        b.sent, b.failed, b.finished = sent, failed, True


_bg_tasks: set[asyncio.Task] = set()


@app.post("/broadcast")
async def broadcast_send(text: str = Form(...), target: str = Form("all"), _=Depends(require_admin)):
    async with session_scope() as s:
        b = Broadcast(text=text, target=target)
        s.add(b)
        await s.flush()
        bid = b.id
    task = asyncio.create_task(_run_broadcast(bid, text, target))
    _bg_tasks.add(task)
    task.add_done_callback(_bg_tasks.discard)
    return RedirectResponse("/broadcast", status_code=303)


async def _bot_username() -> str:
    from ..bot.setup import get_bot

    try:
        return (await get_bot().me()).username
    except Exception:
        return "your_bot"
