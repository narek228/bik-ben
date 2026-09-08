"""
Платежи:
  - ЮMoney — оплата в рублях
  - ЮKassa — карты/СБП
  - NOWPayments — криптовалюта
"""

import hashlib
import hmac
import uuid
from urllib.parse import urlencode

import aiohttp

from config import (
    YOOKASSA_SHOP_ID,
    YOOKASSA_SECRET_KEY,
    NOWPAYMENTS_API_KEY,
    NOWPAYMENTS_IPN_SECRET,
    PUBLIC_WEBHOOK_URL,
)

# ============================================================
# ЮMONEY
# ============================================================

YOOMONEY_PAYMENT_URL = "https://yoomoney.ru/quickpay/confirm"


def get_yoomoney_wallet() -> str:
    try:
        from config import YOOMONEY_WALLET
    except ImportError:
        raise RuntimeError(
            "В config.py не найден YOOMONEY_WALLET. "
            "Добавь переменную YOOMONEY_WALLET."
        )

    if not YOOMONEY_WALLET:
        raise RuntimeError("YOOMONEY_WALLET не заполнен.")

    return str(YOOMONEY_WALLET).strip()


def create_yoomoney_payment_link(amount_rub: float, label: str) -> str:
    """Создаёт ссылку на оплату ЮMoney с уникальным label."""

    params = {
        "receiver": get_yoomoney_wallet(),
        "quickpay-form": "shop",
        "targets": "Пополнение баланса Telegram-бота",
        "paymentType": "AC",
        "sum": f"{amount_rub:.2f}",
        "label": label,
    }

    return f"{YOOMONEY_PAYMENT_URL}?{urlencode(params)}"


def verify_yoomoney_notification(form_data: dict) -> bool:
    """
    Проверяет SHA-1 подпись HTTP-уведомления ЮMoney.
    """

    try:
        from config import YOOMONEY_NOTIFICATION_SECRET
    except ImportError:
        return False

    if not YOOMONEY_NOTIFICATION_SECRET:
        return False

    received_hash = str(form_data.get("sha1_hash", "")).strip().lower()

    if not received_hash:
        return False

    signature_string = "&".join(
        [
            str(form_data.get("operation_id", "")),
            str(form_data.get("amount", "")),
            str(form_data.get("currency", "")),
            str(form_data.get("datetime", "")),
            str(form_data.get("sender", "")),
            str(form_data.get("codepro", "")),
            str(YOOMONEY_NOTIFICATION_SECRET),
            str(form_data.get("label", "")),
        ]
    )

    calculated_hash = hashlib.sha1(
        signature_string.encode("utf-8")
    ).hexdigest().lower()

    return hmac.compare_digest(calculated_hash, received_hash)


# ============================================================
# ЮKASSA
# ============================================================

YOOKASSA_API_URL = "https://api.yookassa.ru/v3/payments"


async def create_yookassa_payment(
    tg_id: int,
    amount_rub: float,
    credits_to_add: float,
) -> dict:
    """Создаёт платёж ЮKassa."""

    if not YOOKASSA_SHOP_ID:
        raise RuntimeError("YOOKASSA_SHOP_ID не заполнен.")

    if not YOOKASSA_SECRET_KEY:
        raise RuntimeError("YOOKASSA_SECRET_KEY не заполнен.")

    idempotence_key = str(uuid.uuid4())

    payload = {
        "amount": {
            "value": f"{amount_rub:.2f}",
            "currency": "RUB",
        },
        "capture": True,
        "confirmation": {
            "type": "redirect",
            "return_url": "https://t.me/",
        },
        "description": f"Пополнение баланса на {credits_to_add} кредитов",
        "metadata": {
            "tg_id": str(tg_id),
            "credits": str(credits_to_add),
        },
    }

    auth = aiohttp.BasicAuth(
        YOOKASSA_SHOP_ID,
        YOOKASSA_SECRET_KEY,
    )

    headers = {
        "Idempotence-Key": idempotence_key,
        "Content-Type": "application/json",
    }

    timeout = aiohttp.ClientTimeout(total=30)

    async with aiohttp.ClientSession(timeout=timeout) as session:
        async with session.post(
            YOOKASSA_API_URL,
            json=payload,
            auth=auth,
            headers=headers,
        ) as resp:
            data = await resp.json()

            if resp.status not in (200, 201):
                raise RuntimeError(f"YooKassa error: {data}")

    try:
        confirmation_url = data["confirmation"]["confirmation_url"]
        payment_id = data["id"]
    except (KeyError, TypeError):
        raise RuntimeError(
            f"YooKassa вернула неожиданный ответ: {data}"
        )

    return {
        "confirmation_url": confirmation_url,
        "payment_id": payment_id,
    }


def verify_yookassa_webhook(request_ip: str) -> bool:
    return True


async def check_yookassa_payment_status(payment_id: str) -> str:
    """Проверяет статус платежа ЮKassa."""

    if not YOOKASSA_SHOP_ID:
        raise RuntimeError("YOOKASSA_SHOP_ID не заполнен.")

    if not YOOKASSA_SECRET_KEY:
        raise RuntimeError("YOOKASSA_SECRET_KEY не заполнен.")

    auth = aiohttp.BasicAuth(
        YOOKASSA_SHOP_ID,
        YOOKASSA_SECRET_KEY,
    )

    timeout = aiohttp.ClientTimeout(total=30)

    async with aiohttp.ClientSession(timeout=timeout) as session:
        async with session.get(
            f"{YOOKASSA_API_URL}/{payment_id}",
            auth=auth,
        ) as resp:
            data = await resp.json()

            if resp.status != 200:
                raise RuntimeError(
                    f"YooKassa status error: {data}"
                )

    return data.get("status", "pending")


# ============================================================
# NOWPAYMENTS
# ============================================================

NOWPAYMENTS_API_URL = "https://api.nowpayments.io/v1"


async def create_crypto_invoice(
    tg_id: int,
    amount_usd: float,
    credits_to_add: float,
) -> dict:
    """Создаёт крипто-инвойс NOWPayments."""

    if not NOWPAYMENTS_API_KEY:
        raise RuntimeError("NOWPAYMENTS_API_KEY не заполнен.")

    headers = {
        "x-api-key": NOWPAYMENTS_API_KEY,
        "Content-Type": "application/json",
    }

    payload = {
        "price_amount": amount_usd,
        "price_currency": "usd",
        "order_id": f"{tg_id}:{uuid.uuid4()}",
        "order_description": f"Пополнение на {credits_to_add} кредитов",
        "ipn_callback_url": (
            f"{PUBLIC_WEBHOOK_URL}/webhook/nowpayments"
        ),
    }

    timeout = aiohttp.ClientTimeout(total=30)

    async with aiohttp.ClientSession(timeout=timeout) as session:
        async with session.post(
            f"{NOWPAYMENTS_API_URL}/invoice",
            json=payload,
            headers=headers,
        ) as resp:
            data = await resp.json()

            if resp.status != 200:
                raise RuntimeError(
                    f"NOWPayments error: {data}"
                )

    try:
        invoice_url = data["invoice_url"]
        payment_id = str(data["id"])
    except (KeyError, TypeError):
        raise RuntimeError(
            f"NOWPayments вернул неожиданный ответ: {data}"
        )

    return {
        "invoice_url": invoice_url,
        "payment_id": payment_id,
    }


def verify_nowpayments_signature(
    raw_body: bytes,
    signature_header: str,
) -> bool:
    """Проверяет HMAC-SHA512 подпись NOWPayments IPN."""

    if not NOWPAYMENTS_IPN_SECRET:
        return False

    if not signature_header:
        return False

    computed = hmac.new(
        NOWPAYMENTS_IPN_SECRET.encode("utf-8"),
        raw_body,
        hashlib.sha512,
    ).hexdigest()

    return hmac.compare_digest(
        computed.lower(),
        signature_header.lower(),
    )
