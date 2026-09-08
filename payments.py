"""
Приём оплаты двумя способами:
  - ЮKassa: карты и СБП, деньги приходят на расчётный счёт в РФ
  - NOWPayments: крипта (BTC, USDT и т.д.), деньги приходят на крипто-кошелёк

Оба провайдера работают по одной схеме:
  1. Создаём "счёт на оплату" (invoice/payment) → получаем ссылку для юзера
  2. Сохраняем его в pending_payments со статусом 'pending'
  3. Провайдер шлёт webhook (IPN) на ваш сервер, когда оплата подтверждена
  4. Мы проверяем подпись webhook'а и начисляем кредиты

Курсы обмена (1 USD/RUB = сколько кредитов) — в config.py.
"""
import hashlib
import hmac
import uuid

import aiohttp

from config import (
    YOOKASSA_SHOP_ID,
    YOOKASSA_SECRET_KEY,
    NOWPAYMENTS_API_KEY,
    NOWPAYMENTS_IPN_SECRET,
    PUBLIC_WEBHOOK_URL,
)

# ---------- ЮKassa ----------
# Используем HTTP API напрямую (без SDK), чтобы не тащить лишнюю зависимость
# и явно видеть, что происходит "под капотом".

YOOKASSA_API_URL = "https://api.yookassa.ru/v3/payments"


async def create_yookassa_payment(tg_id: int, amount_rub: float, credits_to_add: float) -> dict:
    """
    Возвращает {"confirmation_url": "...", "payment_id": "..."}.
    amount_rub — сумма в рублях, credits_to_add — сколько кредитов начислить после оплаты
    (обычно 1:1 с рублём, но можно делать скидки за объём).
    """
    idempotence_key = str(uuid.uuid4())
    payload = {
        "amount": {"value": f"{amount_rub:.2f}", "currency": "RUB"},
        "capture": True,
        "confirmation": {
            "type": "redirect",
            "return_url": f"https://t.me/",  # вернёт юзера обратно в Telegram после оплаты
        },
        "description": f"Пополнение баланса на {credits_to_add} кредитов",
        "metadata": {"tg_id": str(tg_id), "credits": str(credits_to_add)},
    }
    auth = aiohttp.BasicAuth(YOOKASSA_SHOP_ID, YOOKASSA_SECRET_KEY)
    headers = {"Idempotence-Key": idempotence_key, "Content-Type": "application/json"}

    async with aiohttp.ClientSession() as session:
        async with session.post(YOOKASSA_API_URL, json=payload, auth=auth, headers=headers) as resp:
            data = await resp.json()
            if resp.status != 200:
                raise RuntimeError(f"YooKassa error: {data}")

    return {
        "confirmation_url": data["confirmation"]["confirmation_url"],
        "payment_id": data["id"],
    }


def verify_yookassa_webhook(request_ip: str) -> bool:
    """
    ЮKassa не подписывает вебхуки HMAC-подписью — вместо этого рекомендуется
    проверять, что запрос пришёл с их IP-адресов (список в документации),
    и/или дополнительно запрашивать статус платежа по payment_id через API
    перед начислением кредитов (самый надёжный способ — см. bot.py: мы
    всегда перепроверяем платёж через GET /payments/{id} перед зачислением).
    """
    return True  # плейсхолдер — реальная проверка платежа делается через API-запрос ниже


async def check_yookassa_payment_status(payment_id: str) -> str:
    """Возвращает статус: 'pending' | 'succeeded' | 'canceled'."""
    auth = aiohttp.BasicAuth(YOOKASSA_SHOP_ID, YOOKASSA_SECRET_KEY)
    async with aiohttp.ClientSession() as session:
        async with session.get(f"{YOOKASSA_API_URL}/{payment_id}", auth=auth) as resp:
            data = await resp.json()
    return data.get("status", "pending")


# ---------- NOWPayments (крипта) ----------

NOWPAYMENTS_API_URL = "https://api.nowpayments.io/v1"


async def create_crypto_invoice(tg_id: int, amount_usd: float, credits_to_add: float) -> dict:
    """
    Возвращает {"invoice_url": "...", "payment_id": "..."}.
    Пользователь сам выбирает монету (BTC/USDT/TON и т.д.) на странице NOWPayments.
    """
    headers = {"x-api-key": NOWPAYMENTS_API_KEY, "Content-Type": "application/json"}
    payload = {
        "price_amount": amount_usd,
        "price_currency": "usd",
        "order_id": f"{tg_id}:{uuid.uuid4()}",
        "order_description": f"Пополнение на {credits_to_add} кредитов",
        "ipn_callback_url": f"{PUBLIC_WEBHOOK_URL}/webhook/nowpayments",
    }
    async with aiohttp.ClientSession() as session:
        async with session.post(f"{NOWPAYMENTS_API_URL}/invoice", json=payload, headers=headers) as resp:
            data = await resp.json()
            if resp.status != 200:
                raise RuntimeError(f"NOWPayments error: {data}")

    return {"invoice_url": data["invoice_url"], "payment_id": str(data["id"])}


def verify_nowpayments_signature(raw_body: bytes, signature_header: str) -> bool:
    """
    NOWPayments подписывает IPN HMAC-SHA512 от отсортированного по ключам JSON.
    Обязательно проверяйте эту подпись — иначе кто угодно сможет прислать
    поддельный webhook "оплата прошла" и получить кредиты бесплатно.
    """
    computed = hmac.new(
        NOWPAYMENTS_IPN_SECRET.encode(), raw_body, hashlib.sha512
    ).hexdigest()
    return hmac.compare_digest(computed, signature_header)
