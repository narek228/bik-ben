"""
Унифицированный клиент для двух провайдеров.
Возвращает не только текст ответа, но и точную стоимость запроса в кредитах —
это нужно, чтобы списать с пользователя ровно столько, сколько потрачено
(плюс наценка), а не фиксированную цену "за сообщение".
"""
from dataclasses import dataclass
import os

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

# LMRouter / OpenRouter-подобные шлюзы часто ожидают эти заголовки.
_DEFAULT_HEADERS = {
    "HTTP-Referer": os.getenv("HTTP_REFERER", "https://t.me"),
    "X-Title": os.getenv("X_TITLE", "Telegram Bot"),
}

openai_client = (
    AsyncOpenAI(
        api_key=OPENAI_API_KEY,
        base_url=OPENAI_BASE_URL,
        default_headers=_DEFAULT_HEADERS,
    )
    if OPENAI_API_KEY
    else None
)
anthropic_client = (
    AsyncAnthropic(api_key=ANTHROPIC_API_KEY, base_url=ANTHROPIC_BASE_URL)
    if ANTHROPIC_API_KEY
    else None
)

def _normalize_image_base(url: str | None) -> str | None:
    if not url:
        return None
    value = url.strip().rstrip("/")
    # Совместимость со старым адресом LMRouter.
    if "mlrouter.ru" in value:
        return "https://api.lmrouter.com/openai/v1"
    if value == "https://api.lmrouter.com":
        return "https://api.lmrouter.com/openai/v1"
    return value


_image_base = _normalize_image_base(IMAGE_BASE_URL or OPENAI_BASE_URL)
image_client = None
if OPENAI_API_KEY:
    if _image_base and _image_base != OPENAI_BASE_URL:
        image_client = AsyncOpenAI(
            api_key=OPENAI_API_KEY,
            base_url=_image_base,
            default_headers=_DEFAULT_HEADERS,
        )
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
    return _tokens_to_credits(model, avg_input_tokens, max_output_tokens)


def _auth_error_hint(base: str, model: str) -> str:
    return (
        f"401 Unauthorized при генерации картинки.\n"
        f"Запрос шёл на: {base}\n"
        f"Модель: {model}\n\n"
        f"Это ответ сервера LMRouter/прокси: ключ не принят.\n\n"
        f"Проверьте в Railway / .env:\n"
        f"1. OPENAI_API_KEY — именно ключ из кабинета LMRouter (не OpenAI)\n"
        f"2. Ключ активен, есть баланс, image-модели разрешены\n"
        f"3. IMAGE_MODEL — точный слаг из каталога (попробуйте openai/gpt-5-image)\n"
        f"4. OPENAI_BASE_URL / IMAGE_BASE_URL = https://api.lmrouter.com/openai/v1\n\n"
        f"Быстрый тест: работает ли обычный текстовый чат с тем же ключом?"
    )


async def generate_image(prompt: str, model: str | None = None) -> ImageResult:
    if not OPENAI_API_KEY:
        raise RuntimeError(
            "OPENAI_API_KEY не задан в .env — генерация картинок недоступна"
        )

    model = model or IMAGE_MODEL
    base = (_image_base or "https://api.openai.com/v1").rstrip("/")
    # LMRouter uses the OpenAI-compatible /openai/v1 image endpoint.

    # --- Путь 1: официальный клиент ---
    if image_client is not None:
        try:
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

            last_client_error = e
        else:
            last_client_error = None
    else:
        last_client_error = None

    # --- Путь 2: raw aiohttp ---
    url = f"{base}/images/generations"
    headers = {
        "Authorization": f"Bearer {OPENAI_API_KEY}",
        "Content-Type": "application/json",
        **_DEFAULT_HEADERS,
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
