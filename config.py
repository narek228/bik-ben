"""
Конфигурация бота. Все секреты берутся из .env — никогда не хардкодьте
ключи прямо в коде, особенно если планируете выкладывать код в репозиторий.
"""
import os
from dotenv import load_dotenv

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN", "")

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")

# Если используете российский прокси-сервис (ProxyAPI и подобные) вместо
# официальных серверов OpenAI/Anthropic — впишите его адрес сюда через .env.
# Если оставить пустым — используются официальные серверы напрямую.
# Пример для ProxyAPI: OPENAI_BASE_URL=https://api.proxyapi.ru/openai/v1
OPENAI_BASE_URL = os.getenv("OPENAI_BASE_URL", "") or None
ANTHROPIC_BASE_URL = os.getenv("ANTHROPIC_BASE_URL", "") or None

# Отдельный URL для генерации картинок. У LMRouter адрес для картинок может
# отличаться от текстового. Если переменная не задана — код использует
# OPENAI_BASE_URL как запасной вариант.
# В Railway → Variables задайте, например:
#   IMAGE_BASE_URL=https://api.lmrouter.com/openai/v1
#   или IMAGE_BASE_URL=https://api.proxyapi.ru/openai/v1
IMAGE_BASE_URL = os.getenv("IMAGE_BASE_URL", "") or None

YOOKASSA_SHOP_ID = os.getenv("YOOKASSA_SHOP_ID", "")
YOOKASSA_SECRET_KEY = os.getenv("YOOKASSA_SECRET_KEY", "")

NOWPAYMENTS_API_KEY = os.getenv("NOWPAYMENTS_API_KEY", "")
NOWPAYMENTS_IPN_SECRET = os.getenv("NOWPAYMENTS_IPN_SECRET", "")

# ЮMoney (бывшие Яндекс.Деньги) — приём на личный кошелёк, без OAuth.
YOOMONEY_WALLET = os.getenv("YOOMONEY_WALLET", "")
YOOMONEY_NOTIFICATION_SECRET = os.getenv("YOOMONEY_NOTIFICATION_SECRET", "")

PUBLIC_WEBHOOK_URL = os.getenv("PUBLIC_WEBHOOK_URL", "")

# Во сколько раз цена для пользователя выше себестоимости API-запроса.
MARKUP_MULTIPLIER = float(os.getenv("MARKUP_MULTIPLIER", "3.0"))

# Курс для перевода долларов в "кредиты" внутреннего баланса.
# 1 кредит = 1 рубль, чтобы пользователю были понятны цифры.
USD_TO_CREDIT_RATE = float(os.getenv("USD_TO_CREDIT_RATE", "100"))

# Названия моделей — точные слаги из каталога вашего провайдера.
MODEL_PRICING = {
    "gpt-4o-mini": {"input": 0.15, "output": 0.60},
    "gpt-4o": {"input": 2.50, "output": 10.00},
    "claude-haiku-4-5-20251001": {"input": 1.00, "output": 5.00},
    "anthropic-claude-sonnet-4-6": {"input": 3.00, "output": 15.00},
}

DEFAULT_MODEL = "gpt-4o-mini"

# ---------- Генерация изображений ----------
# Слаг модели для картинок. По умолчанию dall-e-3 (официальный OpenAI / большинство прокси).
# Если используете LMRouter / другой агрегатор — укажите актуальный слаг из их каталога
# через переменную IMAGE_MODEL в Railway / .env (не правьте код).
IMAGE_MODEL = os.getenv("IMAGE_MODEL", "dall-e-3")

# Фиксированная цена одной картинки в кредитах.
IMAGE_COST_CREDITS = float(os.getenv("IMAGE_COST_CREDITS", "15"))

# Сколько кредитов дать новому пользователю бесплатно
SIGNUP_BONUS_CREDITS = 20

# Сколько последних сообщений диалога хранить
MAX_HISTORY_MESSAGES = 16

# Режимы — системные промпты
PROMPT_MODES = {
    "default": {
        "label": "Обычный ассистент",
        "system_prompt": None,
    },
    "copywriter": {
        "label": "Копирайтер",
        "system_prompt": (
            "Ты профессиональный копирайтер. Пиши цепляющие, живые тексты "
            "для рекламы, соцсетей и маркетинга. Предлагай несколько вариантов, "
            "если это уместно. Избегай канцелярита и шаблонных фраз."
        ),
    },
    "psychologist": {
        "label": "Психолог-собеседник",
        "system_prompt": (
            "Ты внимательный собеседник с эмпатией, помогающий человеку разобраться "
            "в мыслях и чувствах через диалог. Не ставь диагнозы, не давай медицинских "
            "советов. При признаках острого кризиса мягко порекомендуй обратиться "
            "к специалисту или на горячую линию поддержки."
        ),
    },
    "repetitor": {
        "label": "Репетитор",
        "system_prompt": (
            "Ты терпеливый репетитор. Объясняй сложные темы простыми словами, "
            "используй примеры и аналогии, проверяй понимание вопросами, "
            "не выдавай сразу готовый ответ на учебные задачи — веди к решению."
        ),
    },
}

# ---------- Реферальная программа ----------
REFERRAL_BONUS_FOR_REFERRER = 15
REFERRAL_BONUS_FOR_NEWCOMER = 10

DB_PATH = "bot_database.sqlite3"
