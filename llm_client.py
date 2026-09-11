"""
Унифицированный клиент для двух провайдеров.
Возвращает не только текст ответа, но и точную стоимость запроса в кредитах —
это нужно, чтобы списать с пользователя ровно столько, сколько потрачено
(плюс наценка), а не фиксированную цену "за сообщение".
"""
from dataclasses import dataclass

import aiohttp
from openai import AsyncOpenAI
from anthropic import AsyncAnthropic

from config import (
    OPENAI_API_KEY,
    ANTHROPIC_API_KEY,
    OPENAI_BASE_URL,
    ANTHROPIC_BASE_URL,
    IMAGE_BASE_URL,
    IMAGE_MODEL,
    MODEL_PRICING,
    MARKUP_MULTIPLIER,
    USD_TO_CREDIT_RATE,
)

# base_url=None означает "использовать официальный сервер по умолчанию" —
# так работает и для прямого доступа, и для российских прокси вроде ProxyAPI,
# если в .env указан OPENAI_BASE_URL / ANTHROPIC_BASE_URL.
openai_client = (
    AsyncOpenAI(api_key=OPENAI_API_KEY, base_url=OPENAI_BASE_URL) if OPENAI_API_KEY else None
)
anthropic_client = (
    AsyncAnthropic(api_key=ANTHROPIC_API_KEY, base_url=ANTHROPIC_BASE_URL) if ANTHROPIC_API_KEY else None
)

# Отдельный клиент для картинок: если IMAGE_BASE_URL задан и отличается от
# OPENAI_BASE_URL — используем его. Иначе тот же openai_client.
_image_base = IMAGE_BASE_URL or OPENAI_BASE_URL
image_client = None
if OPENAI_API_KEY:
    if IMAGE_BASE_URL and IMAGE_BASE_URL != OPENAI_BASE_URL:
        image_client = AsyncOpenAI(api_key=OPENAI_API_KEY, base_url=IMAGE_BASE_URL)
    else:
        image_client = openai_client

OPENAI_MODELS = {"gpt-4o-mini", "gpt-4o"}
ANTHROPIC_MODELS = {"claude-haiku-4-5-20251001", "anthropic-claude-sonnet-4-6"}


@dataclass
class GenerationResult:
    text: str
    cost_credits: float
    input_tokens: int
    output_tokens: int


@dataclass
class ImageResult:
    kind: str  # "url" или "b64"
    data: str  # сама ссылка, либо base64-строка данных картинки


def _tokens_to_credits(model: str, input_tokens: int, output_tokens: int) -> float:
    pricing = MODEL_PRICING[model]
    cost_usd = (
        input_tokens / 1_000_000 * pricing["input"]
        + output_tokens / 1_000_000 * pricing["output"]
    )
    cost_usd_with_markup = cost_usd * MARKUP_MULTIPLIER
    return round(cost_usd_with_markup * USD_TO_CREDIT_RATE, 4)


async def generate(
    model: str,
    user_message: str,
    history: list[dict] | None = None,
    system_prompt: str | None = None,
) -> GenerationResult:
    """
    history — список {"role": "user"/"assistant", "content": "..."} для контекста диалога.
    Если не нужен — оставьте None, бот будет отвечать без памяти прошлых сообщений.
    system_prompt — задаёт "роль"/поведение модели на весь диалог (режимы /mode).
    """
    history = history or []

    if model in OPENAI_MODELS:
        if not openai_client:
            raise RuntimeError("OPENAI_API_KEY не задан в .env")
        messages = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages += history + [{"role": "user", "content": user_message}]
        resp = await openai_client.chat.completions.create(model=model, messages=messages)
        text = resp.choices[0].message.content or ""
        usage = resp.usage
        cost = _tokens_to_credits(model, usage.prompt_tokens, usage.completion_tokens)
        return GenerationResult(text, cost, usage.prompt_tokens, usage.completion_tokens)

    elif model in ANTHROPIC_MODELS:
        if anthropic_client:
            messages = history + [{"role": "user", "content": user_message}]
            kwargs = {"model": model, "max_tokens": 1024, "messages": messages}
            if system_prompt:
                kwargs["system"] = system_prompt
            resp = await anthropic_client.messages.create(**kwargs)
            text = "".join(block.text for block in resp.content if block.type == "text")
            cost = _tokens_to_credits(model, resp.usage.input_tokens, resp.usage.output_tokens)
            return GenerationResult(text, cost, resp.usage.input_tokens, resp.usage.output_tokens)

        elif openai_client:
            messages = []
            if system_prompt:
                messages.append({"role": "system", "content": system_prompt})
            messages += history + [{"role": "user", "content": user_message}]
            resp = await openai_client.chat.completions.create(model=model, messages=messages)
            text = resp.choices[0].message.content or ""
            usage = resp.usage
            cost = _tokens_to_credits(model, usage.prompt_tokens, usage.completion_tokens)
            return GenerationResult(text, cost, usage.prompt_tokens, usage.completion_tokens)

        else:
            raise RuntimeError(
                "Нет доступа к Claude: задайте ANTHROPIC_API_KEY, либо OPENAI_API_KEY "
                "+ OPENAI_BASE_URL с агрегатором, который поддерживает модели Claude"
            )

    else:
        raise ValueError(f"Неизвестная модель: {model}")


def estimate_max_cost(model: str, max_output_tokens: int = 1024, avg_input_tokens: int = 200) -> float:
    """Грубая оценка стоимости — используется, чтобы заранее проверить,
    хватит ли у пользователя баланса, ДО отправки запроса в API."""
    return _tokens_to_credits(model, avg_input_tokens, max_output_tokens)


def _auth_error_hint(base: str, model: str) -> str:
    return (
        f"401 Unauthorized при генерации картинки.\n"
        f"Запрос шёл на: {base}\n"
        f"Модель: {model}\n\n"
        f"Что проверить в Railway / .env:\n"
        f"1. OPENAI_API_KEY — ключ от ТОГО ЖЕ провайдера, чей URL указан\n"
        f"2. IMAGE_BASE_URL (или OPENAI_BASE_URL) — правильный адрес API\n"
        f"   Примеры:\n"
        f"   • ProxyAPI: https://api.proxyapi.ru/openai/v1\n"
        f"   • LMRouter: https://api.lmrouter.com/openai/v1\n"
        f"   • Официальный OpenAI: https://api.openai.com/v1\n"
        f"3. IMAGE_MODEL — слаг модели из каталога вашего провайдера\n"
        f"4. Ключ не истёк и у него есть доступ к image-моделям"
    )


async def generate_image(prompt: str, model: str | None = None) -> ImageResult:
    """
    Генерация изображения через OpenAI-совместимый images API.

    Сначала пробуем официальный AsyncOpenAI-клиент (корректная авторизация).
    Если не получилось — fallback на raw aiohttp.
    """
    if not OPENAI_API_KEY:
        raise RuntimeError(
            "OPENAI_API_KEY не задан в .env — генерация картинок недоступна"
        )

    model = model or IMAGE_MODEL
    base = (_image_base or "https://api.openai.com/v1").rstrip("/")

    # --- Путь 1: официальный клиент (предпочтительный) ---
    if image_client is not None:
        try:
            # response_format=b64_json удобнее для Telegram
            resp = await image_client.images.generate(
                model=model,
                prompt=prompt,
                n=1,
                response_format="b64_json",
            )
            item = resp.data[0]
            if getattr(item, "b64_json", None):
                return ImageResult(kind="b64", data=item.b64_json)
            if getattr(item, "url", None):
                return ImageResult(kind="url", data=item.url)
            raise RuntimeError(f"В ответе клиента нет b64_json/url: {item}")
        except Exception as e:
            err_str = str(e).lower()
            # Если провайдер не любит response_format — пробуем без него
            if "response_format" in err_str or "unknown parameter" in err_str:
                try:
                    resp = await image_client.images.generate(
                        model=model,
                        prompt=prompt,
                        n=1,
                    )
                    item = resp.data[0]
                    if getattr(item, "b64_json", None):
                        return ImageResult(kind="b64", data=item.b64_json)
                    if getattr(item, "url", None):
                        return ImageResult(kind="url", data=item.url)
                except Exception as e2:
                    e = e2

            if "401" in str(e) or "unauthorized" in str(e).lower() or "invalid_api_key" in str(e).lower():
                raise RuntimeError(_auth_error_hint(base, model)) from e

            # Не падаем сразу — пробуем raw aiohttp как запасной путь
            last_client_error = e
        else:
            last_client_error = None
    else:
        last_client_error = None

    # --- Путь 2: raw aiohttp (для экзотических провайдеров) ---
    url = f"{base}/images/generations"
    headers = {
        "Authorization": f"Bearer {OPENAI_API_KEY}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": model,
        "prompt": prompt,
        "n": 1,
        "response_format": "b64_json",
    }

    async with aiohttp.ClientSession() as session:
        try:
            async with session.post(
                url,
                json=payload,
                headers=headers,
                timeout=aiohttp.ClientTimeout(total=180),
            ) as resp:
                try:
                    data = await resp.json(content_type=None)
                except Exception:
                    data = {"raw_response": await resp.text()}

                if resp.status == 401:
                    raise RuntimeError(_auth_error_hint(base, model))

                if resp.status != 200:
                    # Повтор без response_format
                    if resp.status in (400, 422) and "response_format" in str(data).lower():
                        payload.pop("response_format", None)
                        async with session.post(
                            url,
                            json=payload,
                            headers=headers,
                            timeout=aiohttp.ClientTimeout(total=180),
                        ) as resp2:
                            try:
                                data = await resp2.json(content_type=None)
                            except Exception:
                                data = {"raw_response": await resp2.text()}
                            if resp2.status == 401:
                                raise RuntimeError(_auth_error_hint(base, model))
                            if resp2.status != 200:
                                raise RuntimeError(f"Image API → {resp2.status}: {data}")
                    else:
                        raise RuntimeError(f"Image API → {resp.status}: {data}")

                items = data.get("data")
                if not items:
                    if data.get("url"):
                        return ImageResult(kind="url", data=data["url"])
                    if data.get("b64_json"):
                        return ImageResult(kind="b64", data=data["b64_json"])
                    raise RuntimeError(f"Неожиданный ответ image API: {data}")

                item = items[0]
                if item.get("b64_json"):
                    return ImageResult(kind="b64", data=item["b64_json"])
                if item.get("url"):
                    return ImageResult(kind="url", data=item["url"])
                for key in ("b64", "base64", "image", "image_base64"):
                    if item.get(key):
                        return ImageResult(kind="b64", data=item[key])
                raise RuntimeError(f"В ответе нет url / b64_json: {data}")

        except aiohttp.ClientError as e:
            extra = f" (также клиент: {last_client_error})" if last_client_error else ""
            raise RuntimeError(f"Ошибка соединения с image API: {e}{extra}") from e
