import asyncio
from datetime import timedelta
from types import SimpleNamespace

import pytest

from anonchat.db import engine, init_db, session_scope
from anonchat.models import Base, Gender, Rating, User, UserState, utcnow
from anonchat.services import matching, rooms
from anonchat.services.users import get_or_create_user


_loop = asyncio.new_event_loop()


def run(coro):
    return _loop.run_until_complete(coro)


@pytest.fixture(autouse=True)
def fresh_db():
    async def reset():
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.drop_all)
        await init_db()

    run(reset())


async def mk(uid, gender=None, age=None, vip=False, **kw):
    async with session_scope() as s:
        tg = SimpleNamespace(id=uid, username=f"u{uid}", first_name="T", language_code="ru")
        u, _ = await get_or_create_user(s, tg)
        u.gender, u.age = gender, age
        if vip:
            u.vip_until = utcnow() + timedelta(days=1)
        for k, v in kw.items():
            setattr(u, k, v)
    return uid


async def state(uid):
    async with session_scope() as s:
        return (await s.get(User, uid)).state


def test_basic_match_and_end():
    async def scenario():
        await mk(1)
        await mk(2)
        assert await matching.start_search(1) is None
        m = await matching.start_search(2)
        assert m and {m.user_a.id, m.user_b.id} == {1, 2}
        assert await state(1) == UserState.chatting
        res = await matching.end_dialog(1)
        assert res and res[1] == 2
        assert await state(2) == UserState.idle
        # сразу с прошлым собеседником не соединяет…
        assert await matching.start_search(1) is None
        assert await matching.start_search(2) is None
        # …но после 30 секунд ожидания — можно
        async with session_scope() as s:
            for uid in (1, 2):
                (await s.get(User, uid)).search_since = utcnow() - timedelta(minutes=1)
        assert len(await matching.match_pending()) == 1
    run(scenario())


def test_vip_gender_filter_both_ways():
    async def scenario():
        await mk(1, Gender.male, 20, vip=True, pref_gender=Gender.female)
        await mk(2, Gender.male, 22)
        await mk(3, Gender.female, 21)
        assert await matching.start_search(1) is None
        assert await matching.start_search(2) is None   # парень не подходит VIP-у
        m = await matching.start_search(3)
        assert m and {m.user_a.id, m.user_b.id} == {1, 3}
    run(scenario())


def test_vip_priority():
    async def scenario():
        await mk(1, Gender.female, 20)                                   # ждёт дольше, без VIP
        await mk(2, Gender.female, 20, vip=True, pref_gender=Gender.male)  # VIP ищет парня
        await mk(3, Gender.male, 20)
        await matching.start_search(1)
        await asyncio.sleep(0.01)
        assert await matching.start_search(2) is None  # 1 и 2 друг другу не подходят
        m = await matching.start_search(3)
        assert {m.user_a.id, m.user_b.id} == {2, 3}     # VIP идёт первым
        assert await state(1) == UserState.searching
    run(scenario())


def test_dislike_blocks_rematch():
    async def scenario():
        await mk(1)
        await mk(2)
        await matching.start_search(1)
        m = await matching.start_search(2)
        await matching.end_dialog(1)
        async with session_scope() as s:
            s.add(Rating(dialog_id=m.dialog_id, from_user_id=1, to_user_id=2, value=-1))
        assert await matching.start_search(1) is None
        assert await matching.start_search(2) is None
        async with session_scope() as s:
            for uid in (1, 2):
                (await s.get(User, uid)).search_since = utcnow() - timedelta(minutes=5)
        assert await matching.match_pending() == []
        await mk(3)
        assert await matching.start_search(3) is not None      # даже после ослабления фильтров
        assert await matching.start_search(2) is None
    run(scenario())


def test_rooms_join_leave():
    async def scenario():
        await mk(1)
        await mk(2)
        items, total = await rooms.list_public_rooms()
        assert total >= 8
        room_id = next(i.room.id for i in items if not i.room.vip_only)
        _, m1, others = await rooms.join_room(1, room_id)
        assert others == []
        _, m2, others = await rooms.join_room(2, room_id)
        assert others == [1]
        vip_room = next(i.room.id for i in items if i.room.vip_only)
        with pytest.raises(rooms.RoomError):
            await rooms.join_room(1, vip_room)
        await rooms.leave_room(1)
        assert await state(1) == UserState.idle
        assert [m.user_id for m in await rooms.room_members(room_id)] == [2]
    run(scenario())


def test_admin_pages_render():
    from fastapi.testclient import TestClient

    from anonchat.admin.app import app
    import scripts.demo_data as demo

    run(demo.main())
    c = TestClient(app)
    assert c.get("/", follow_redirects=False).status_code == 303
    r = c.post("/login", data={"login": "admin", "password": "secret", "next": "/"}, follow_redirects=False)
    assert r.status_code == 303
    for url in ["/", "/live", "/dialogs", "/dialogs?status=active", "/dialogs/1", "/dialogs/1/messages?after=0",
                "/rooms", "/rooms/1", "/rooms/1/messages", "/users", "/users?filter=vip", "/users/100001",
                "/messages", "/messages?q=Привет", "/gallery", "/reports", "/payments", "/broadcast"]:
        resp = c.get(url)
        assert resp.status_code == 200, (url, resp.text[:500])
