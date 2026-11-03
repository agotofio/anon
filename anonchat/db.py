from __future__ import annotations

import os
from contextlib import asynccontextmanager
from typing import AsyncIterator

from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from .config import get_settings
from .models import Base

_settings = get_settings()

if _settings.database_url.startswith("sqlite"):
    path = _settings.database_url.split("///", 1)[-1]
    if path and path != ":memory:":
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)

engine = create_async_engine(_settings.database_url, future=True, pool_pre_ping=True)

if _settings.database_url.startswith("sqlite"):

    @event.listens_for(engine.sync_engine, "connect")
    def _sqlite_pragmas(dbapi_conn, _):  # pragma: no cover - инфраструктура
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA journal_mode=WAL")
        cur.execute("PRAGMA foreign_keys=ON")
        cur.execute("PRAGMA busy_timeout=5000")
        cur.close()


SessionLocal = async_sessionmaker(engine, expire_on_commit=False)


@asynccontextmanager
async def session_scope() -> AsyncIterator[AsyncSession]:
    async with SessionLocal() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


async def init_db() -> None:
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    from .services.rooms import seed_default_rooms

    await seed_default_rooms()
