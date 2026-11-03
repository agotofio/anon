from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    bot_token: str = ""
    admin_ids: list[int] = []

    database_url: str = "sqlite+aiosqlite:///data/anonchat.db"

    admin_host: str = "0.0.0.0"
    admin_port: int = 8080
    admin_login: str = "admin"
    admin_password: str = "change-me-please"
    secret_key: str = "replace-with-long-random-string"
    admin_public_url: str = ""

    media_cache_dir: str = "data/media"
    support_username: str = ""

    # Антифлуд: не больше N сообщений за WINDOW секунд
    flood_limit: int = 6
    flood_window: float = 4.0

    # Бесплатные ограничения
    free_rooms_limit: int = 0          # сколько комнат может создать обычный пользователь
    vip_rooms_limit: int = 5           # сколько комнат может создать VIP
    room_default_size: int = 30
    room_max_size_vip: int = 100

    referral_bonus_days: int = 1       # сколько дней VIP за приглашённого друга

    @field_validator("admin_ids", mode="before")
    @classmethod
    def _split_ids(cls, v):
        if isinstance(v, str):
            return [int(x) for x in v.replace(" ", "").split(",") if x]
        if isinstance(v, int):
            return [v]
        return v


@lru_cache
def get_settings() -> Settings:
    return Settings()


# ---------------------------------------------------------------------------
# Тарифы. Оплата в Telegram Stars (XTR) — единственный разрешённый Telegram
# способ продажи цифровых услуг внутри ботов. Ориентир: 1 ⭐ ≈ 1,6–2 ₽
# (≈ $0.013–0.02 в зависимости от способа покупки звёзд), т.е. цены ниже
# сопоставимы с подписками популярных анонимных чатов (99–399 ₽/мес).
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class Product:
    code: str
    title: str
    description: str
    stars: int
    vip_days: int = 0          # 0 — не VIP-продукт; -1 — навсегда
    emoji: str = "👑"


VIP_FOREVER = -1

PRODUCTS: dict[str, Product] = {
    p.code: p
    for p in [
        Product("vip_1d", "VIP на 1 день", "Попробовать все возможности VIP 24 часа", 25, 1, "⚡"),
        Product("vip_7d", "VIP на 7 дней", "Неделя приоритетного поиска и фильтров", 99, 7, "🥉"),
        Product("vip_30d", "VIP на 30 дней", "Самый популярный тариф — выгода 40%", 249, 30, "🥈"),
        Product("vip_90d", "VIP на 90 дней", "Три месяца — выгода 55%", 549, 90, "🥇"),
        Product("vip_forever", "VIP навсегда", "Один платёж — VIP без срока", 1990, VIP_FOREVER, "💎"),
    ]
}

# Добровольные донаты (поддержка проекта, без привилегий; от 100 ⭐ — бонусный VIP)
DONATE_AMOUNTS: list[int] = [10, 50, 100, 250, 500, 1000]
DONATE_VIP_BONUS: dict[int, int] = {100: 3, 250: 7, 500: 14, 1000: 30}  # ⭐ -> дней VIP в подарок
