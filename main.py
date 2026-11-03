"""Точка входа: запускает Telegram-бота и веб-админку в одном процессе.

    python main.py            # бот + админка
    python main.py bot        # только бот
    python main.py admin      # только админка
"""
from __future__ import annotations

import asyncio
import logging
import sys

import uvicorn

from anonchat.config import get_settings
from anonchat.db import init_db

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("anonchat")


async def run_bot() -> None:
    from anonchat.bot.common import matcher_loop
    from anonchat.bot.setup import build_dispatcher, get_bot, setup_bot_profile

    bot = get_bot()
    dp = build_dispatcher()
    await setup_bot_profile(bot)
    matcher = asyncio.create_task(matcher_loop(bot))
    me = await bot.me()
    log.info("Бот @%s запущен", me.username)
    try:
        await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())
    finally:
        matcher.cancel()


async def run_admin() -> None:
    from anonchat.admin.app import app

    s = get_settings()
    config = uvicorn.Config(app, host=s.admin_host, port=s.admin_port, log_level="info")
    log.info("Админ-панель: http://%s:%s", s.admin_host, s.admin_port)
    await uvicorn.Server(config).serve()


async def main(mode: str) -> None:
    s = get_settings()
    await init_db()
    tasks = []
    if mode in ("all", "bot"):
        if not s.bot_token:
            log.error("BOT_TOKEN не задан — заполните .env")
            sys.exit(1)
        tasks.append(run_bot())
    if mode in ("all", "admin"):
        if s.admin_password == "change-me-please":
            log.warning("⚠️  Задайте ADMIN_PASSWORD в .env — сейчас стоит пароль по умолчанию!")
        tasks.append(run_admin())
    await asyncio.gather(*tasks)


if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else "all"
    try:
        asyncio.run(main(mode))
    except KeyboardInterrupt:
        pass
