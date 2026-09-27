"""
Точка входа. Запускает:
  1. Telegram-бота (aiogram, long polling)
  2. Небольшой aiohttp-сервер для приёма webhook'ов от NOWPayments

Запуск: python bot.py
Требуется заполненный .env (см. .env.example).
"""
import asyncio
import logging
import re
import uuid
import base64

from aiogram import Bot, Dispatcher, F
from aiogram.filters import CommandStart, Command, CommandObject
from aiogram.types import (
    Message,
    CallbackQuery,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
    ReplyKeyboardMarkup,
    KeyboardButton,
    BufferedInputFile,
)
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
    "anthropic-claude-sonnet-4-6": "Claude (мощный, Sonnet)",
}

TOPUP_OPTIONS_RUB = [100, 300, 1000]

BTN_MODEL = "🧠 Модель"
BTN_MODE = "🎭 Режим"
BTN_IMAGE = "🖼 Картинка"
BTN_BALANCE = "💳 Баланс"
BTN_TOPUP = "💰 Пополнить"
BTN_CLEAR = "🧹 Очистить память"
BTN_REF = "👥 Реферальная ссылка"
BTN_HELP = "❓ Помощь"

# Фразы, по которым обычное сообщение считается запросом на картинку
IMAGE_TRIGGER_RE = re.compile(
    r"^\s*("
    r"нарисуй|нарисовать|нарисуйте|"
    r"сгенерируй\s+(картинку|изображение|image)|"
    r"сгенерировать\s+(картинку|изображение)|"
    r"создай\s+(картинку|изображение)|"
    r"сделай\s+(картинку|изображение|рисунок)|"
    r"draw|generate\s+image|image:|/image"
    r")\s*[:\-–]?\s*",
    re.IGNORECASE | re.UNICODE,
)


def main_menu_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text=BTN_MODEL), KeyboardButton(text=BTN_MODE)],
            [KeyboardButton(text=BTN_IMAGE), KeyboardButton(text=BTN_BALANCE)],
            [KeyboardButton(text=BTN_TOPUP), KeyboardButton(text=BTN_CLEAR)],
            [KeyboardButton(text=BTN_REF), KeyboardButton(text=BTN_HELP)],
        ],
        resize_keyboard=True,
    )

HELP_TEXT = (
    "<b>Команды бота</b>\n\n"
    "/model — выбрать модель (ChatGPT / Claude)\n"
    "/mode — выбрать режим общения (обычный, копирайтер, репетитор и т.д.)\n"
    "/image [описание] — сгенерировать картинку\n"
    "/topup — пополнить баланс\n"
    "/balance — проверить баланс\n"
    "/history — последние операции с балансом\n"
    "/clear — стереть память текущего диалога (начать с чистого листа)\n"
    "/ref — реферальная ссылка и статистика приглашений\n"
    "/help — это сообщение\n\n"
    "Просто напиши сообщение — отвечу через выбранную модель.\n"
    "Если начнёшь с «нарисуй …» или «сгенерируй картинку …» — сразу сделаю картинку.\n"
    "Внизу есть меню с быстрыми кнопками."
)


@dp.message(CommandStart())
async def cmd_start(message: Message, command: CommandObject):
    tg_id = message.from_user.id
    referred_by = None
    if command.args and command.args.startswith("ref_"):
        try:
            referred_by = int(command.args[4:])
        except ValueError:
            referred_by = None

    is_new_user = not await db.user_exists(tg_id)
    user = await db.get_or_create_user(
        tg_id,
        message.from_user.username,
        referred_by=referred_by,
    )

    # Реферальный бонус выдаём только при самой первой регистрации.
    if (
        is_new_user
        and referred_by
        and referred_by != tg_id
        and user.get("referred_by") == referred_by
    ):
        await db.add_credits(
            tg_id,
            config.REFERRAL_BONUS_FOR_NEWCOMER,
            "referral_bonus_newcomer",
        )
        await db.add_credits(
            referred_by,
            config.REFERRAL_BONUS_FOR_REFERRER,
            "referral_bonus_referrer",
        )
        try:
            await bot.send_message(
                referred_by,
                f"По твоей реферальной ссылке зарегистрировался новый пользователь! "
                f"Начислено {config.REFERRAL_BONUS_FOR_REFERRER} кредитов.",
            )
        except Exception:
            pass
        user = await db.get_or_create_user(
            tg_id, message.from_user.username
        )

    await message.answer(
        f"Привет! Я бот-обёртка над ChatGPT и Claude.\n\n"
        f"Твой баланс: <b>{user['balance']:.1f} кредитов</b>\n"
        f"Текущая модель: <b>{MODEL_LABELS.get(user['model'], user['model'])}</b>\n\n"
        f"{HELP_TEXT}",
        reply_markup=main_menu_keyboard(),
    )


@dp.message(Command("help"))
async def cmd_help(message: Message):
    await message.answer(HELP_TEXT, reply_markup=main_menu_keyboard())


@dp.message(Command("balance"))
async def cmd_balance(message: Message):
    balance = await db.get_balance(message.from_user.id)
    await message.answer(f"Баланс: <b>{balance:.1f} кредитов</b>")


@dp.message(Command("history"))
async def cmd_history(message: Message):
    txns = await db.get_transactions(message.from_user.id, limit=15)
    if not txns:
        await message.answer("Пока нет ни одной операции.")
        return

    lines = ["<b>Последние операции:</b>\n"]
    for t in txns:
        sign = "+" if t["amount"] > 0 else ""
        lines.append(f"{t['created_at'][:16]} — {sign}{t['amount']:.1f} ({t['reason']})")
    await message.answer("\n".join(lines))


@dp.message(Command("clear"))
async def cmd_clear(message: Message):
    await db.clear_history(message.from_user.id)
    await message.answer("Память диалога очищена — начинаем с чистого листа.")


@dp.message(Command("ref"))
async def cmd_ref(message: Message):
    tg_id = message.from_user.id
    bot_info = await bot.get_me()
    link = f"https://t.me/{bot_info.username}?start=ref_{tg_id}"
    count = await db.count_referrals(tg_id)
    await message.answer(
        f"Твоя реферальная ссылка:\n{link}\n\n"
        f"Приглашено пользователей: <b>{count}</b>\n"
        f"За каждого нового пользователя ты получаешь "
        f"{config.REFERRAL_BONUS_FOR_REFERRER} кредитов, "
        f"а новичок — дополнительно {config.REFERRAL_BONUS_FOR_NEWCOMER}."
    )


@dp.message(Command("mode"))
async def cmd_mode(message: Message):
    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=info["label"], callback_data=f"setmode:{key}")]
            for key, info in config.PROMPT_MODES.items()
        ]
    )
    await message.answer("Выбери режим общения:", reply_markup=kb)


@dp.callback_query(F.data.startswith("setmode:"))
async def cb_set_mode(callback: CallbackQuery):
    mode = callback.data.split(":", 1)[1]
    if mode not in config.PROMPT_MODES:
        await callback.answer("Неизвестный режим.", show_alert=True)
        return
    await db.set_mode(callback.from_user.id, mode)
    label = config.PROMPT_MODES[mode]["label"]
    await callback.message.edit_text(f"Режим установлен: <b>{label}</b>")
    await callback.answer()


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
    if model not in MODEL_LABELS:
        await callback.answer("Неизвестная модель.", show_alert=True)
        return
    await db.set_model(callback.from_user.id, model)
    await callback.message.edit_text(f"Модель установлена: <b>{MODEL_LABELS[model]}</b>")
    await callback.answer()


@dp.message(Command("topup"))
async def cmd_topup(message: Message):
    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=f"{amount}₽ — ЮMoney", callback_data=f"topup:yoomoney:{amount}"
                ),
            ]
            for amount in TOPUP_OPTIONS_RUB
        ]
        + [
            [
                InlineKeyboardButton(
                    text=f"{amount}₽ — карта (ЮKassa)", callback_data=f"topup:yookassa:{amount}"
                ),
            ]
            for amount in TOPUP_OPTIONS_RUB
        ]
    )
    await message.answer("Выбери сумму и способ пополнения:", reply_markup=kb)


@dp.callback_query(F.data.startswith("topup:"))
async def cb_topup(callback: CallbackQuery):
    parts = callback.data.split(":")
    if len(parts) != 3 or parts[1] not in {"yoomoney", "yookassa"}:
        await callback.answer("Некорректный платёж.", show_alert=True)
        return

    _, provider, amount_str = parts
    try:
        amount = float(amount_str)
    except ValueError:
        await callback.answer("Некорректная сумма.", show_alert=True)
        return

    if amount not in TOPUP_OPTIONS_RUB:
        await callback.answer("Некорректная сумма.", show_alert=True)
        return

    tg_id = callback.from_user.id
    credits_to_add = amount

    try:
        if provider == "yoomoney":
            label = str(uuid.uuid4())
            link = payments.create_yoomoney_payment_link(amount, label)
            await db.create_pending_payment(label, tg_id, "yoomoney", credits_to_add)
            await callback.message.answer(
                f"Оплати {amount}₽ по ссылке, кредиты начислятся автоматически:\n{link}"
            )
        elif provider == "yookassa":
            result = await payments.create_yookassa_payment(tg_id, amount, credits_to_add)
            await db.create_pending_payment(result["payment_id"], tg_id, "yookassa", credits_to_add)
            await callback.message.answer(
                f"Оплати {amount}₽ по ссылке, кредиты начислятся автоматически:\n{result['confirmation_url']}"
            )
        await callback.answer()
    except Exception as e:
        log.exception("Ошибка создания платежа")
        await callback.message.answer("Не удалось создать платёж. Попробуй ещё раз позже.")
        await callback.answer()


@dp.message(Command("image"))
async def cmd_image(message: Message, command: CommandObject):
    prompt = command.args
    if not prompt:
        await message.answer(
            "Опиши, что нарисовать, после команды. Например:\n"
            "<code>/image рыжий кот в скафандре на Марсе</code>\n\n"
            "Или просто напиши: <code>нарисуй рыжий кот в скафандре на Марсе</code>"
        )
        return
    await run_image_generation(message, prompt)


async def run_image_generation(message: Message, prompt: str):
    tg_id = message.from_user.id
    user = await db.get_or_create_user(tg_id, message.from_user.username)

    prompt = (prompt or "").strip()
    if not prompt:
        await message.answer("Нужно описание картинки.")
        return

    if user["balance"] < config.IMAGE_COST_CREDITS:
        await message.answer(
            f"Недостаточно кредитов на картинку (нужно {config.IMAGE_COST_CREDITS}, "
            f"у тебя {user['balance']:.1f}).\nПополни баланс: /topup"
        )
        return

    charged = await db.try_charge(tg_id, config.IMAGE_COST_CREDITS, reason="image_generation")
    if not charged:
        await message.answer("Недостаточно кредитов. Пополни баланс: /topup")
        return

    await bot.send_chat_action(message.chat.id, "upload_photo")

    try:
        result = await llm_client.generate_image(prompt, config.IMAGE_MODEL)
        if result.kind == "url":
            await message.answer_photo(photo=result.data, caption=f"«{prompt}»")
        else:
            image_bytes = base64.b64decode(result.data)
            photo = BufferedInputFile(image_bytes, filename="image.png")
            await message.answer_photo(photo=photo, caption=f"«{prompt}»")
    except Exception as e:
        log.exception("Ошибка генерации картинки")
        await db.refund(tg_id, config.IMAGE_COST_CREDITS, reason="refund_image_error")
        await message.answer(
            f"Не удалось сгенерировать картинку, кредиты возвращены.\n\n"
            f"Ошибка: <code>{e}</code>\n\n"
            f"Проверьте IMAGE_MODEL и IMAGE_BASE_URL в .env / Railway. "
            f"Слаг модели должен совпадать с каталогом вашего провайдера."
        )


@dp.message(F.text == BTN_MODEL)
async def btn_model(message: Message):
    await cmd_model(message)


@dp.message(F.text == BTN_MODE)
async def btn_mode(message: Message):
    await cmd_mode(message)


@dp.message(F.text == BTN_BALANCE)
async def btn_balance(message: Message):
    await cmd_balance(message)


@dp.message(F.text == BTN_TOPUP)
async def btn_topup(message: Message):
    await cmd_topup(message)


@dp.message(F.text == BTN_CLEAR)
async def btn_clear(message: Message):
    await cmd_clear(message)


@dp.message(F.text == BTN_REF)
async def btn_ref(message: Message):
    await cmd_ref(message)


@dp.message(F.text == BTN_HELP)
async def btn_help(message: Message):
    await cmd_help(message)


@dp.message(F.text == BTN_IMAGE)
async def btn_image(message: Message):
    await message.answer(
        "Напиши описание картинки одним из способов:\n\n"
        "• <code>/image рыжий кот в скафандре на Марсе</code>\n"
        "• <code>нарисуй рыжий кот в скафандре на Марсе</code>\n"
        "• <code>сгенерируй картинку: закат над океаном</code>"
    )


def _extract_image_prompt(text: str) -> str | None:
    """Если сообщение — запрос на картинку, возвращает очищенный промпт."""
    if not text:
        return None
    m = IMAGE_TRIGGER_RE.match(text)
    if not m:
        return None
    prompt = text[m.end():].strip()
    return prompt if prompt else None


@dp.message(F.text)
async def handle_generation(message: Message):
    text = (message.text or "").strip()

    # Автоопределение запроса на картинку по ключевым словам
    image_prompt = _extract_image_prompt(text)
    if image_prompt is not None:
        await run_image_generation(message, image_prompt)
        return

    tg_id = message.from_user.id
    user = await db.get_or_create_user(tg_id, message.from_user.username)
    model = user["model"]
    mode = user.get("mode", "default")
    system_prompt = config.PROMPT_MODES.get(mode, {}).get("system_prompt")

    estimated_cost = llm_client.estimate_max_cost(model)
    if user["balance"] < estimated_cost:
        await message.answer(
            f"Недостаточно кредитов (нужно ~{estimated_cost:.1f}, у тебя {user['balance']:.1f}).\n"
            f"Пополни баланс: /topup"
        )
        return

    charged = await db.try_charge(tg_id, estimated_cost, reason="generation_reserved")
    if not charged:
        await message.answer("Недостаточно кредитов. Пополни баланс: /topup")
        return

    await bot.send_chat_action(message.chat.id, "typing")

    history = await db.get_recent_messages(tg_id)

    try:
        result = await llm_client.generate(
            model, text, history=history, system_prompt=system_prompt
        )
    except Exception as e:
        log.exception("Ошибка генерации")
        await db.refund(tg_id, estimated_cost, reason="refund_api_error")
        await message.answer(f"Ошибка при обращении к модели, кредиты возвращены. ({e})")
        return

    difference = estimated_cost - result.cost_credits
    if difference > 0:
        await db.refund(tg_id, difference, reason="refund_overestimate")

    await db.add_message(tg_id, "user", text)
    await db.add_message(tg_id, "assistant", result.text)

    await message.answer(result.text)


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
            completed = await db.complete_pending_payment(payment_id, "topup_crypto")
            if completed:
                try:
                    await bot.send_message(
                        completed["tg_id"],
                        f"Оплата получена! Начислено {completed['amount_credits']} кредитов.",
                    )
                except Exception:
                    pass

    return web.Response(status=200, text="ok")


async def handle_yoomoney_webhook(request: web.Request):
    """ЮMoney уведомления об оплате."""
    form = await request.post()
    form_dict = dict(form)

    if not payments.verify_yoomoney_notification(form_dict):
        log.warning("Неверная подпись уведомления ЮMoney — запрос отклонён")
        return web.Response(status=400, text="invalid signature")

    label = form_dict.get("label", "")
    pending = await db.get_pending_payment(label)
    if pending and pending["status"] == "pending":
        completed = await db.complete_pending_payment(label, "topup_yoomoney")
        if completed:
            try:
                await bot.send_message(
                    completed["tg_id"],
                    f"Оплата получена! Начислено {completed['amount_credits']} кредитов.",
                )
            except Exception:
                pass

    return web.Response(status=200, text="OK")


async def handle_yookassa_webhook(request: web.Request):
    """ЮKassa уведомления об оплате."""
    try:
        data = await request.json()
    except Exception:
        return web.Response(status=400, text="invalid json")

    event = data.get("event")
    payment_obj = data.get("object", {})
    payment_id = payment_obj.get("id")
    status = payment_obj.get("status")

    if event == "payment.succeeded" and status == "succeeded":
        pending = await db.get_pending_payment(payment_id)
        if pending and pending["status"] == "pending":
            completed = await db.complete_pending_payment(
                payment_id, "topup_yookassa"
            )
            if completed:
                try:
                    await bot.send_message(
                        completed["tg_id"],
                        f"Оплата получена! Начислено {completed['amount_credits']} кредитов.",
                    )
                except Exception:
                    pass

    return web.Response(status=200, text="ok")


async def start_webhook_server():
    app = web.Application()
    app.router.add_post("/webhook/nowpayments", handle_nowpayments_webhook)
    app.router.add_post("/webhook/yoomoney", handle_yoomoney_webhook)
    app.router.add_post("/webhook/yookassa", handle_yookassa_webhook)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", 8080)
    await site.start()
    log.info("Webhook-сервер запущен на порту 8080")


async def poll_yookassa_pending_payments():
    """Проверяем статусы ЮKassa платежей раз в 10 секунд."""
    import aiosqlite

    while True:
        await asyncio.sleep(10)
        try:
            async with aiosqlite.connect(config.DB_PATH) as conn:
                conn.row_factory = aiosqlite.Row
                cur = await conn.execute(
                    "SELECT * FROM pending_payments "
                    "WHERE provider = 'yookassa' AND status = 'pending'"
                )
                rows = await cur.fetchall()

            for row in rows:
                try:
                    status = await payments.check_yookassa_payment_status(
                        row["payment_id"]
                    )
                except Exception:
                    log.exception(
                        "Ошибка проверки статуса ЮKassa для %s",
                        row["payment_id"],
                    )
                    continue

                if status == "succeeded":
                    completed = await db.complete_pending_payment(
                        row["payment_id"], "topup_yookassa"
                    )
                    if completed:
                        try:
                            await bot.send_message(
                                completed["tg_id"],
                                f"Оплата получена! Начислено "
                                f"{completed['amount_credits']} кредитов.",
                            )
                        except Exception:
                            pass
        except Exception:
            log.exception("Ошибка в poll_yookassa_pending_payments")


async def main():
    await db.init_db()
    await start_webhook_server()
    asyncio.create_task(poll_yookassa_pending_payments())
    log.info("Бот запущен, начинаю polling...")
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
