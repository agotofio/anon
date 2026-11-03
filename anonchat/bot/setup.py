from __future__ import annotations

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.types import BotCommand, LinkPreviewOptions

from ..config import get_settings
from .handlers import admin, profile, relay, rooms, search, start, vip
from .middlewares import FloodMiddleware, UserMiddleware

_bot: Bot | None = None


def get_bot() -> Bot:
    """Единый экземпляр Bot (используется и ботом, и админкой для скачивания медиа/рассылок)."""
    global _bot
    if _bot is None:
        _bot = Bot(
            token=get_settings().bot_token,
            default=DefaultBotProperties(
                parse_mode=ParseMode.HTML, link_preview_options=LinkPreviewOptions(is_disabled=True)
            ),
        )
    return _bot


def build_dispatcher() -> Dispatcher:
    dp = Dispatcher()
    user_mw = UserMiddleware()
    dp.message.outer_middleware(user_mw)
    dp.edited_message.outer_middleware(user_mw)
    dp.callback_query.outer_middleware(user_mw)
    dp.pre_checkout_query.outer_middleware(user_mw)
    dp.message.middleware(FloodMiddleware())

    # порядок важен: relay — последним
    dp.include_routers(admin.router, start.router, vip.router, search.router, rooms.router, profile.router, relay.router)
    return dp


COMMANDS = [
    BotCommand(command="search", description="🔍 Найти собеседника"),
    BotCommand(command="next", description="⏭ Следующий собеседник"),
    BotCommand(command="stop", description="⛔ Закончить диалог"),
    BotCommand(command="rooms", description="👥 Групповые комнаты"),
    BotCommand(command="vip", description="👑 VIP-статус"),
    BotCommand(command="profile", description="👤 Профиль"),
    BotCommand(command="settings", description="⚙️ Настройки"),
    BotCommand(command="link", description="🔗 Отправить свой @username"),
    BotCommand(command="report", description="🚩 Пожаловаться"),
    BotCommand(command="invite", description="🎁 Пригласить друга"),
    BotCommand(command="donate", description="💝 Поддержать проект"),
    BotCommand(command="help", description="❓ Помощь"),
    BotCommand(command="rules", description="📜 Правила"),
    BotCommand(command="paysupport", description="💳 Вопросы по оплате"),
]


async def setup_bot_profile(bot: Bot) -> None:
    await bot.set_my_commands(COMMANDS)
    try:
        await bot.set_my_short_description("🎭 Анонимный чат: собеседник за 2 секунды, групповые комнаты, VIP")
        await bot.set_my_description(
            "🎭 Анонимный чат\n\n"
            "🔍 Случайный собеседник один на один\n"
            "👥 Анонимные комнаты по интересам\n"
            "👑 VIP: поиск по полу и возрасту, приоритет, свои комнаты\n\n"
            "Нажми «Старт» 👇"
        )
    except Exception:
        pass
