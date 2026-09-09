"""
Точка входа. Запускает:
  1. Telegram-бота (aiogram, long polling)
  2. Небольшой aiohttp-сервер для приёма webhook'ов от NOWPayments

Запуск: python bot.py
Требуется заполненный .env (см. .env.example).
"""
import asyncio
import logging
import uuid

from aiogram import Bot, Dispatcher, F
from aiogram.filters import CommandStart, Command, CommandObject
from aiogram.types import (
    Message,
    CallbackQuery,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
    ReplyKeyboardMarkup,
    KeyboardButton,
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

TOPUP_OPTIONS_RUB = [100, 300, 1000]  # варианты пополнения в рублях/долларах

# Подписи кнопок нижнего меню — сюда же роутится текст, когда юзер их нажимает
# (обычное текстовое сообщение с этим же содержимым, Telegram API так устроен).
BTN_MODEL = "🧠 Модель"
BTN_MODE = "🎭 Режим"
BTN_IMAGE = "🖼 Картинка"
BTN_BALANCE = "💳 Баланс"
BTN_TOPUP = "💰 Пополнить"
BTN_CLEAR = "🧹 Очистить память"
BTN_REF = "👥 Реферальная ссылка"
BTN_HELP = "❓ Помощь"


def main_menu_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text=BTN_MODEL), KeyboardButton(text=BTN_MODE)],
            [KeyboardButton(text=BTN_IMAGE), KeyboardButton(text=BTN_BALANCE)],
            [KeyboardButton(text=BTN_TOPUP), KeyboardButton(text=BTN_CLEAR)],
            [KeyboardButton(text=BTN_REF), KeyboardButton(text=BTN_HELP)],
        ],
        resize_keyboard=True,  # компактный размер кнопок вместо занимания полэкрана
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
    "Просто напиши сообщение — отвечу через выбранную модель, помня контекст "
    "последних сообщений (если не сбросить через /clear). Внизу есть меню "
    "с быстрыми кнопками для тех же действий."
)


# ---------- Команды ----------

@dp.message(CommandStart())
async def cmd_start(message: Message, command: CommandObject):
    tg_id = message.from_user.id

    # Реферальная ссылка выглядит как t.me/bot?start=ref_123456789
    referred_by = None
    if command.args and command.args.startswith("ref_"):
        try:
            referred_by = int(command.args[4:])
        except ValueError:
            referred_by = None

    is_new = (await db.get_balance(tg_id)) == 0 and not await db.get_referrer(tg_id)
    # Более надёжная проверка "новый ли пользователь" — до создания записи в БД.
    existing = await db.get_or_create_user(tg_id, message.from_user.username, referred_by=referred_by)

    user = existing

    # Если реферальная привязка только что удачно применилась — начисляем бонусы.
    actual_referrer = await db.get_referrer(tg_id)
    if referred_by and actual_referrer == referred_by:
        # Проверяем, что бонус ещё не выдавался (по факту наличия транзакции
        # достаточно того, что привязка произошла только что при создании).
        await db.add_credits(tg_id, config.REFERRAL_BONUS_FOR_NEWCOMER, "referral_bonus_newcomer")
        await db.add_credits(referred_by, config.REFERRAL_BONUS_FOR_REFERRER, "referral_bonus_referrer")
        try:
            await bot.send_message(
                referred_by,
                f"По твоей реферальной ссылке зарегистрировался новый пользователь! "
                f"Начислено {config.REFERRAL_BONUS_FOR_REFERRER} кредитов.",
            )
        except Exception:
            pass
        user = await db.get_or_create_user(tg_id, message.from_user.username)

    await message.answer(
        f"Привет! Я бот-обёртка над ChatGPT и Claude.\n\n"
        f"Твой баланс: <b>{user['balance']:.1f} кредитов</b>\n"
        f"Текущая модель: <b>{MODEL_LABELS.get(user['model'], user['model'])}</b>\n\n"
        f"{HELP_TEXT}",
        reply_markup=main_menu_keyboard(),
    )


@dp.message(Command("help"))
async def cmd_help(message: Message):
    await message.answer(HELP_TEXT)


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
    await db.set_model(callback.from_user.id, model)
    await callback.message.edit_text(f"Модель установлена: <b>{MODEL_LABELS[model]}</b>")
    await callback.answer()


@dp.message(Command("topup"))
async def cmd_topup(message: Message):
    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=f"{amount}₽ — карта (ЮMoney)", callback_data=f"topup:yoomoney:{amount}"
                ),
            ]
            for amount in TOPUP_OPTIONS_RUB
        ]
    )
    await message.answer("Выбери сумму пополнения:", reply_markup=kb)


@dp.callback_query(F.data.startswith("topup:"))
async def cb_topup(callback: CallbackQuery):
    _, provider, amount_str = callback.data.split(":")
    amount = float(amount_str)
    tg_id = callback.from_user.id
    credits_to_add = amount  # 1 рубль = 1 кредит, см. config.USD_TO_CREDIT_RATE

    try:
        if provider == "yoomoney":
            # label — наш собственный уникальный идентификатор платежа,
            # ЮMoney вернёт его в уведомлении как есть.
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


@dp.message(Command("image"))
async def cmd_image(message: Message, command: CommandObject):
    prompt = command.args
    if not prompt:
        await message.answer(
            "Опиши, что нарисовать, после команды. Например:\n"
            "<code>/image рыжий кот в скафандре на Марсе</code>"
        )
        return
    await run_image_generation(message, prompt)


async def run_image_generation(message: Message, prompt: str):
    tg_id = message.from_user.id
    user = await db.get_or_create_user(tg_id, message.from_user.username)

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
        image_url = await llm_client.generate_image(prompt, config.IMAGE_MODEL)
        await message.answer_photo(photo=image_url, caption=f"«{prompt}»")
    except Exception as e:
        log.exception("Ошибка генерации картинки")
        await db.refund(tg_id, config.IMAGE_COST_CREDITS, reason="refund_image_error")
        await message.answer(
            f"Не удалось сгенерировать картинку, кредиты возвращены. ({e})\n\n"
            f"Возможная причина: у вашего провайдера другой формат ответа для "
            f"картинок или другое название модели — сверьтесь с их документацией "
            f"и поправьте IMAGE_MODEL в настройках."
        )


# ---------- Кнопки нижнего меню ----------
# Reply-кнопки приходят как обычный текст — роутим на существующие команды.

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
        "Напиши так: <code>/image описание картинки</code>\n"
        "Например: <code>/image рыжий кот в скафандре на Марсе</code>"
    )


# ---------- Генерация ответов ----------

@dp.message(F.text)
async def handle_generation(message: Message):
    tg_id = message.from_user.id
    user = await db.get_or_create_user(tg_id, message.from_user.username)
    model = user["model"]
    mode = user.get("mode", "default")
    system_prompt = config.PROMPT_MODES.get(mode, {}).get("system_prompt")

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

    # Подтягиваем историю диалога ДО текущего сообщения — иначе оно задвоится.
    history = await db.get_recent_messages(tg_id)

    try:
        result = await llm_client.generate(
            model, message.text, history=history, system_prompt=system_prompt
        )
    except Exception as e:
        log.exception("Ошибка генерации")
        await db.refund(tg_id, estimated_cost, reason="refund_api_error")
        await message.answer(f"Ошибка при обращении к модели, кредиты возвращены. ({e})")
        return

    # 3. Корректируем списание: возвращаем разницу между оценкой и фактом.
    difference = estimated_cost - result.cost_credits
    if difference > 0:
        await db.refund(tg_id, difference, reason="refund_overestimate")

    # 4. Сохраняем и вопрос, и ответ в историю — на следующий запрос модель
    # снова увидит этот обмен как часть контекста диалога.
    await db.add_message(tg_id, "user", message.text)
    await db.add_message(tg_id, "assistant", result.text)

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


# ---------- Webhook-сервер для ЮMoney ----------

async def handle_yoomoney_webhook(request: web.Request):
    """
    ЮMoney шлёт POST с form-data (не JSON!) при каждом входящем переводе
    на кошелёк. label — наш идентификатор платежа, который мы сами задали
    при создании ссылки в payments.create_yoomoney_payment_link.
    """
    form = await request.post()
    form_dict = dict(form)

    if not payments.verify_yoomoney_notification(form_dict):
        log.warning("Неверная подпись уведомления ЮMoney — запрос отклонён")
        return web.Response(status=400, text="invalid signature")

    label = form_dict.get("label", "")
    pending = await db.get_pending_payment(label)
    if pending and pending["status"] == "pending":
        await db.add_credits(
            pending["tg_id"], pending["amount_credits"], "topup_yoomoney", label
        )
        await db.mark_payment_paid(label)
        try:
            await bot.send_message(
                pending["tg_id"],
                f"Оплата получена! Начислено {pending['amount_credits']} кредитов.",
            )
        except Exception:
            pass

    # ЮMoney достаточно получить любой ответ 200 OK — тело не проверяется.
    return web.Response(status=200, text="OK")


async def start_webhook_server():
    app = web.Application()
    app.router.add_post("/webhook/nowpayments", handle_nowpayments_webhook)
    app.router.add_post("/webhook/yoomoney", handle_yoomoney_webhook)
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
