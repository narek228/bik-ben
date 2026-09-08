"""
Точка входа. Запускает:
  1. Telegram-бота (aiogram, long polling)
  2. Небольшой aiohttp-сервер для приёма webhook'ов от NOWPayments

Запуск: python bot.py
Требуется заполненный .env (см. .env.example).
"""
import asyncio
import logging

from aiogram import Bot, Dispatcher, F
from aiogram.filters import CommandStart, Command
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.client.default import DefaultBotProperties
from aiohttp import web

import config
import database as db
import llm_client
import payments

logging.basicConfig(level=logging.INFO)
log = logging.getLogger(__name__)

bot = Bot(token=config.BOT_TOKEN, default=DefaultBotProperties(parse_mode="HTML"))
dp = Dispatcher()

MODEL_LABELS = {
    "gpt-4o-mini": "ChatGPT (быстрый, gpt-4o-mini)",
    "gpt-4o": "ChatGPT (мощный, gpt-4o)",
    "claude-haiku-4-5-20251001": "Claude (быстрый, Haiku)",
    "claude-sonnet-4-6": "Claude (мощный, Sonnet)",
}

TOPUP_OPTIONS_RUB = [100, 300, 1000]  # варианты пополнения в рублях/долларах


# ---------- Команды ----------

@dp.message(CommandStart())
async def cmd_start(message: Message):
    user = await db.get_or_create_user(message.from_user.id, message.from_user.username)
    await message.answer(
        f"Привет! Я бот-обёртка над ChatGPT и Claude.\n\n"
        f"Твой баланс: <b>{user['balance']:.1f} кредитов</b>\n"
        f"Текущая модель: <b>{MODEL_LABELS.get(user['model'], user['model'])}</b>\n\n"
        f"Команды:\n"
        f"/model — выбрать модель\n"
        f"/topup — пополнить баланс\n"
        f"/balance — проверить баланс\n\n"
        f"Просто напиши мне сообщение — и я отвечу через выбранную модель."
    )


@dp.message(Command("balance"))
async def cmd_balance(message: Message):
    balance = await db.get_balance(message.from_user.id)
    await message.answer(f"Баланс: <b>{balance:.1f} кредитов</b>")


@dp.message(Command("model"))
async def cmd_model(message: Message):
    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=label, callback_data=f"setmodel:{key}")]
            for key, label in MODEL_LABELS.items()
        ]
    )
    await message.answer("Выбери модель:", reply_markup=kb)


@dp.callback_query(F.data.startswith("setmodel:"))
async def cb_set_model(callback: CallbackQuery):
    model = callback.data.split(":", 1)[1]
    await db.set_model(callback.from_user.id, model)
    await callback.message.edit_text(f"Модель установлена: <b>{MODEL_LABELS[model]}</b>")
    await callback.answer()


@dp.message(Command("topup"))
async def cmd_topup(message: Message):
    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=f"{amount}₽ — крипта", callback_data=f"topup:crypto:{amount}"
                ),
                InlineKeyboardButton(
                    text=f"{amount}₽ — карта/СБП", callback_data=f"topup:yookassa:{amount}"
                ),
            ]
            for amount in TOPUP_OPTIONS_RUB
        ]
    )
    await message.answer("Выбери сумму и способ оплаты:", reply_markup=kb)


@dp.callback_query(F.data.startswith("topup:"))
async def cb_topup(callback: CallbackQuery):
    _, provider, amount_str = callback.data.split(":")
    amount = float(amount_str)
    tg_id = callback.from_user.id
    credits_to_add = amount  # 1 рубль = 1 кредит, см. config.USD_TO_CREDIT_RATE

    try:
        if provider == "yookassa":
            result = await payments.create_yookassa_payment(tg_id, amount, credits_to_add)
            await db.create_pending_payment(result["payment_id"], tg_id, "yookassa", credits_to_add)
            await callback.message.answer(
                f"Оплати {amount}₽ по ссылке, кредиты начислятся автоматически:\n{result['confirmation_url']}"
            )
        elif provider == "crypto":
            amount_usd = round(amount / config.USD_TO_CREDIT_RATE * 100, 2)  # грубая конвертация для примера
            result = await payments.create_crypto_invoice(tg_id, amount_usd, credits_to_add)
            await db.create_pending_payment(result["payment_id"], tg_id, "nowpayments", credits_to_add)
            await callback.message.answer(
                f"Оплати эквивалент {amount}₽ в крипте по ссылке:\n{result['invoice_url']}\n\n"
                f"Кредиты начислятся автоматически после подтверждения в сети."
            )
        await callback.answer()
    except Exception as e:
        log.exception("Ошибка создания платежа")
        await callback.message.answer(f"Не удалось создать платёж: {e}")
        await callback.answer()


# ---------- Генерация ответов ----------

@dp.message(F.text)
async def handle_generation(message: Message):
    tg_id = message.from_user.id
    user = await db.get_or_create_user(tg_id, message.from_user.username)
    model = user["model"]

    # 1. Прикидываем максимальную возможную стоимость и проверяем баланс ДО запроса.
    estimated_cost = llm_client.estimate_max_cost(model)
    if user["balance"] < estimated_cost:
        await message.answer(
            f"Недостаточно кредитов (нужно ~{estimated_cost:.1f}, у тебя {user['balance']:.1f}).\n"
            f"Пополни баланс: /topup"
        )
        return

    # 2. Резервируем (списываем) оценочную стоимость сразу — защита от гонки
    # запросов, если юзер шлёт несколько сообщений одновременно.
    charged = await db.try_charge(tg_id, estimated_cost, reason="generation_reserved")
    if not charged:
        await message.answer("Недостаточно кредитов. Пополни баланс: /topup")
        return

    await bot.send_chat_action(message.chat.id, "typing")

    try:
        result = await llm_client.generate(model, message.text)
    except Exception as e:
        log.exception("Ошибка генерации")
        await db.refund(tg_id, estimated_cost, reason="refund_api_error")
        await message.answer(f"Ошибка при обращении к модели, кредиты возвращены. ({e})")
        return

    # 3. Корректируем списание: возвращаем разницу между оценкой и фактом.
    difference = estimated_cost - result.cost_credits
    if difference > 0:
        await db.refund(tg_id, difference, reason="refund_overestimate")

    await message.answer(result.text)


# ---------- Webhook-сервер для NOWPayments ----------

async def handle_nowpayments_webhook(request: web.Request):
    raw_body = await request.read()
    signature = request.headers.get("x-nowpayments-sig", "")

    if not payments.verify_nowpayments_signature(raw_body, signature):
        log.warning("Неверная подпись webhook NOWPayments — запрос отклонён")
        return web.Response(status=403, text="invalid signature")

    data = await request.json()
    payment_id = str(data.get("payment_id", ""))
    status = data.get("payment_status", "")

    if status in ("finished", "confirmed"):
        pending = await db.get_pending_payment(payment_id)
        if pending and pending["status"] == "pending":
            await db.add_credits(
                pending["tg_id"], pending["amount_credits"], "topup_crypto", payment_id
            )
            await db.mark_payment_paid(payment_id)
            try:
                await bot.send_message(
                    pending["tg_id"],
                    f"Оплата получена! Начислено {pending['amount_credits']} кредитов.",
                )
            except Exception:
                pass

    return web.Response(status=200, text="ok")


async def start_webhook_server():
    app = web.Application()
    app.router.add_post("/webhook/nowpayments", handle_nowpayments_webhook)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", 8080)
    await site.start()
    log.info("Webhook-сервер запущен на порту 8080")


async def poll_yookassa_pending_payments():
    """
    ЮKassa поддерживает вебхуки, но их настройка требует подтверждённого
    HTTPS-домена в личном кабинете. Для старта проще и надёжнее опрашивать
    статус висящих платежей самим — раз в 10 секунд проверяем каждый
    'pending' платёж через API и начисляем кредиты, если он оплачен.
    Когда обзаведётесь доменом — замените на нормальный webhook-хендлер.
    """
    import aiosqlite

    while True:
        await asyncio.sleep(10)
        async with aiosqlite.connect(config.DB_PATH) as conn:
            conn.row_factory = aiosqlite.Row
            cur = await conn.execute(
                "SELECT * FROM pending_payments WHERE provider = 'yookassa' AND status = 'pending'"
            )
            rows = await cur.fetchall()

        for row in rows:
            try:
                status = await payments.check_yookassa_payment_status(row["payment_id"])
            except Exception:
                log.exception("Ошибка проверки статуса ЮKassa для %s", row["payment_id"])
                continue

            if status == "succeeded":
                await db.add_credits(
                    row["tg_id"], row["amount_credits"], "topup_yookassa", row["payment_id"]
                )
                await db.mark_payment_paid(row["payment_id"])
                try:
                    await bot.send_message(
                        row["tg_id"],
                        f"Оплата получена! Начислено {row['amount_credits']} кредитов.",
                    )
                except Exception:
                    pass


# ---------- Точка входа ----------

async def main():
    await db.init_db()
    await start_webhook_server()
    asyncio.create_task(poll_yookassa_pending_payments())
    log.info("Бот запущен, начинаю polling...")
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
