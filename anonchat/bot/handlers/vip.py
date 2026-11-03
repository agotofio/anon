"""VIP-подписка и донаты через Telegram Stars."""
from __future__ import annotations

import logging

from aiogram import Bot, F, Router
from aiogram.filters import Command
from aiogram.types import CallbackQuery, LabeledPrice, Message, PreCheckoutQuery
from sqlalchemy import select

from ...config import DONATE_AMOUNTS, DONATE_VIP_BONUS, PRODUCTS, VIP_FOREVER, get_settings
from ...db import session_scope
from ...models import Payment, User
from ...services.users import extend_vip
from ...utils import plural, vip_status_text
from .. import keyboards as kb
from .. import texts as T
from ..common import safe_send

router = Router(name="vip")
log = logging.getLogger(__name__)

VIP_PERKS = (
    "🎯 <b>Поиск по полу</b> — только девушки или только парни\n"
    "🎂 <b>Фильтр по возрасту</b> собеседника\n"
    "⚡ <b>Приоритет в очереди</b> — находим собеседника первым\n"
    "⭐ <b>Рейтинг собеседника</b> виден сразу при знакомстве\n"
    "🔁 <b>Вернуть собеседника</b> после случайного «Стоп»\n"
    "🛡 <b>Защита от пересылки</b> — твои фото и видео нельзя сохранить или переслать\n"
    "🏠 <b>Свои комнаты</b> — публичные и закрытые, до 100 человек\n"
    "👑 <b>VIP-лаунж</b> и значок VIP в профиле\n"
    "✏️ <b>Свой ник</b> в групповых комнатах"
)


def vip_text(user: User) -> str:
    status = vip_status_text(user) if user.is_vip else "обычный аккаунт"
    return (
        "👑 <b>VIP-статус</b>\n\n"
        f"{VIP_PERKS}\n\n"
        f"Твой статус: <b>{status}</b>\n\n"
        "Оплата — <b>Telegram Stars ⭐</b>: безопасно, в пару касаний, без карт.\n"
        "<i>Купить звёзды можно прямо в Telegram или через @PremiumBot.</i>"
    )


@router.message(Command("vip"))
@router.message(F.text == T.BTN_VIP)
async def cmd_vip(message: Message, user: User):
    await message.answer(vip_text(user), reply_markup=kb.vip_kb(user))


@router.callback_query(F.data.startswith("buy:"))
async def cb_buy(cb: CallbackQuery, bot: Bot):
    product = PRODUCTS.get(cb.data.split(":", 1)[1])
    if product is None:
        await cb.answer("Тариф не найден", show_alert=True)
        return
    await bot.send_invoice(
        chat_id=cb.from_user.id,
        title=f"{product.emoji} {product.title}",
        description=product.description,
        payload=f"vip:{product.code}",
        currency="XTR",
        prices=[LabeledPrice(label=product.title, amount=product.stars)],
        provider_token="",
    )
    await cb.answer()


@router.message(Command("donate"))
@router.message(F.text == T.BTN_DONATE)
async def cmd_donate(message: Message):
    bonus = ", ".join(f"{s}⭐ → {d} {plural(d, 'день', 'дня', 'дней')}" for s, d in DONATE_VIP_BONUS.items())
    await message.answer(
        "💝 <b>Поддержать проект</b>\n\n"
        "Бот живёт на донаты: сервер, модерация, новые функции. "
        "Любая сумма — огромное спасибо ❤️\n\n"
        f"🎁 Бонус VIP за донат: {bonus}",
        reply_markup=kb.donate_kb(),
    )


@router.callback_query(F.data.startswith("donate:"))
async def cb_donate(cb: CallbackQuery, bot: Bot):
    amount = int(cb.data.split(":", 1)[1])
    if amount not in DONATE_AMOUNTS:
        await cb.answer()
        return
    await bot.send_invoice(
        chat_id=cb.from_user.id,
        title="💝 Поддержка проекта",
        description=f"Добровольный донат {amount} ⭐. Спасибо, что помогаешь чату жить!",
        payload=f"donate:{amount}",
        currency="XTR",
        prices=[LabeledPrice(label="Донат", amount=amount)],
        provider_token="",
    )
    await cb.answer()


def _parse_payload(payload: str) -> tuple[str, int, int] | None:
    """-> (product_code, stars, vip_days)"""
    kind, _, value = payload.partition(":")
    if kind == "vip" and value in PRODUCTS:
        p = PRODUCTS[value]
        return p.code, p.stars, p.vip_days
    if kind == "donate" and value.isdigit() and int(value) in DONATE_AMOUNTS:
        stars = int(value)
        return f"donate_{stars}", stars, DONATE_VIP_BONUS.get(stars, 0)
    return None


@router.pre_checkout_query()
async def pre_checkout(query: PreCheckoutQuery):
    parsed = _parse_payload(query.invoice_payload)
    if parsed is None or query.currency != "XTR" or query.total_amount != parsed[1]:
        await query.answer(ok=False, error_message="Тариф устарел, открой /vip заново.")
        return
    await query.answer(ok=True)


@router.message(F.successful_payment)
async def successful_payment(message: Message, bot: Bot):
    sp = message.successful_payment
    parsed = _parse_payload(sp.invoice_payload)
    code, stars, days = parsed if parsed else (sp.invoice_payload, sp.total_amount, 0)
    async with session_scope() as s:
        dup = (await s.execute(select(Payment.id).where(Payment.charge_id == sp.telegram_payment_charge_id))).first()
        if dup:
            return
        user = await s.get(User, message.from_user.id)
        s.add(Payment(
            user_id=user.id, product=code, stars=sp.total_amount, vip_days=days,
            charge_id=sp.telegram_payment_charge_id,
        ))
        user.stars_spent += sp.total_amount
        if days:
            extend_vip(user, days)
        status = vip_status_text(user)

    if code.startswith("donate"):
        text = f"💖 <b>Спасибо за поддержку!</b>\nТы задонатил(а) {sp.total_amount} ⭐"
        if days:
            text += f"\n🎁 Бонус: VIP на {days} {plural(days, 'день', 'дня', 'дней')}"
    else:
        what = "навсегда" if days == VIP_FOREVER else f"на {days} {plural(days, 'день', 'дня', 'дней')}"
        text = f"🎉 <b>VIP активирован {what}!</b>\n\nСтатус: {status}\n\n{VIP_PERKS}"
    await message.answer(text)
    for admin_id in get_settings().admin_ids:
        await safe_send(bot, admin_id, f"💰 +{sp.total_amount} ⭐ · {code} · <code>{message.from_user.id}</code>",
                        disable_notification=True)


@router.message(Command("invite"))
async def cmd_invite(message: Message, user: User, bot: Bot):
    await message.answer(await invite_text(user, bot))


@router.callback_query(F.data == "invite")
async def cb_invite(cb: CallbackQuery, user: User, bot: Bot):
    await cb.message.answer(await invite_text(user, bot))
    await cb.answer()


async def invite_text(user: User, bot: Bot) -> str:
    me = await bot.me()
    days = get_settings().referral_bonus_days
    return (
        "🎁 <b>Приглашай друзей — получай VIP</b>\n\n"
        f"За каждого нового пользователя по твоей ссылке — <b>+{days} {plural(days, 'день', 'дня', 'дней')} VIP</b>.\n\n"
        f"🔗 Твоя ссылка:\n<code>https://t.me/{me.username}?start=ref_{user.id}</code>\n\n"
        f"Приглашено: <b>{user.referrals_count}</b>"
    )
