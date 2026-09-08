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

YOOKASSA_SHOP_ID = os.getenv("YOOKASSA_SHOP_ID", "")
YOOKASSA_SECRET_KEY = os.getenv("YOOKASSA_SECRET_KEY", "")

NOWPAYMENTS_API_KEY = os.getenv("NOWPAYMENTS_API_KEY", "")
NOWPAYMENTS_IPN_SECRET = os.getenv("NOWPAYMENTS_IPN_SECRET", "")

# ЮMoney (бывшие Яндекс.Деньги) — приём на личный кошелёк, без OAuth.
# YOOMONEY_WALLET — номер кошелька (виден в настройках yoomoney.ru).
# YOOMONEY_NOTIFICATION_SECRET — "секретное слово", которое ЮMoney выдаёт
# на странице https://yoomoney.ru/transfer/myservices/http-notification
# при подключении HTTP-уведомлений.
YOOMONEY_WALLET = os.getenv("YOOMONEY_WALLET", "")
YOOMONEY_NOTIFICATION_SECRET = os.getenv("YOOMONEY_NOTIFICATION_SECRET", "")

PUBLIC_WEBHOOK_URL = os.getenv("PUBLIC_WEBHOOK_URL", "")

# Во сколько раз цена для пользователя выше себестоимости API-запроса.
# Например: если запрос стоит вам $0.01, а множитель 3.0 — с юзера спишется $0.03.
MARKUP_MULTIPLIER = float(os.getenv("MARKUP_MULTIPLIER", "3.0"))

# Курс для перевода долларов в "кредиты" внутреннего баланса.
# 1 кредит = 1 рубль, чтобы пользователю были понятны цифры.
# Стоимость токенов у провайдеров указана в USD, поэтому нужен курс USD->RUB.
USD_TO_CREDIT_RATE = float(os.getenv("USD_TO_CREDIT_RATE", "100"))  # ~курс ЦБ, обновляйте вручную или через API

# Названия моделей ниже — точные слаги из каталога ML Router (mlrouter.ru).
# Если подключаете другого провайдера/агрегатора — сверьтесь с его списком
# моделей, слаги могут отличаться (например без префикса "anthropic-").
MODEL_PRICING = {
    "gpt-4o-mini": {"input": 0.15, "output": 0.60},
    "gpt-4o": {"input": 2.50, "output": 10.00},
    "claude-haiku-4-5-20251001": {"input": 1.00, "output": 5.00},
    "anthropic-claude-sonnet-4-6": {"input": 3.00, "output": 15.00},
}

DEFAULT_MODEL = "gpt-4o-mini"

# Сколько кредитов дать новому пользователю бесплатно (маркетинг / тест-драйв)
SIGNUP_BONUS_CREDITS = 20

DB_PATH = "bot_database.sqlite3"
