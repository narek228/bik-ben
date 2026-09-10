"""
Унифицированный клиент для двух провайдеров.
Возвращает не только текст ответа, но и точную стоимость запроса в кредитах —
это нужно, чтобы списать с пользователя ровно столько, сколько потрачено
(плюс наценка), а не фиксированную цену "за сообщение".
"""
from dataclasses import dataclass
import re
import base64

import aiohttp
from openai import AsyncOpenAI
from anthropic import AsyncAnthropic

from config import (
    OPENAI_API_KEY,
    ANTHROPIC_API_KEY,
    OPENAI_BASE_URL,
    ANTHROPIC_BASE_URL,
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
        text = resp.choices[0].message.content
        usage = resp.usage
        cost = _tokens_to_credits(model, usage.prompt_tokens, usage.completion_tokens)
        return GenerationResult(text, cost, usage.prompt_tokens, usage.completion_tokens)

    elif model in ANTHROPIC_MODELS:
        if anthropic_client:
            # Прямой доступ через официальный Anthropic API (или ANTHROPIC_BASE_URL=прокси
            # именно с anthropic-совместимым протоколом /v1/messages).
            # У Anthropic системный промпт — отдельный параметр, а не сообщение в списке.
            messages = history + [{"role": "user", "content": user_message}]
            kwargs = {"model": model, "max_tokens": 1024, "messages": messages}
            if system_prompt:
                kwargs["system"] = system_prompt
            resp = await anthropic_client.messages.create(**kwargs)
            text = "".join(block.text for block in resp.content if block.type == "text")
            cost = _tokens_to_credits(model, resp.usage.input_tokens, resp.usage.output_tokens)
            return GenerationResult(text, cost, resp.usage.input_tokens, resp.usage.output_tokens)

        elif openai_client:
            # Fallback: агрегаторы вроде ML Router / OpenRouter отдают Claude через
            # тот же OpenAI-совместимый /v1/chat/completions — отдельный ключ
            # Anthropic не нужен, используем уже настроенный openai_client.
            # Здесь системный промпт идёт как обычное сообщение role=system.
            messages = []
            if system_prompt:
                messages.append({"role": "system", "content": system_prompt})
            messages += history + [{"role": "user", "content": user_message}]
            resp = await openai_client.chat.completions.create(model=model, messages=messages)
            text = resp.choices[0].message.content
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


async def generate_image(prompt: str, model: str) -> ImageResult:
    """Генерация изображения через официальный API LMRouter."""
    if not OPENAI_API_KEY:
        raise RuntimeError(
            "OPENAI_API_KEY не задан в .env — генерация картинок недоступна"
        )

    base = "https://api.lmrouter.com/openai/v1"
    url = f"{base}/images/generations"

    headers = {
        "Authorization": f"Bearer {OPENAI_API_KEY}",
        "Content-Type": "application/json",
    }

    payload = {
        "model": model,
        "prompt": prompt,
        "n": 1,
    }

    async with aiohttp.ClientSession() as session:
        try:
            async with session.post(
                url,
                json=payload,
                headers=headers,
                timeout=aiohttp.ClientTimeout(total=120),
            ) as resp:
                try:
                    data = await resp.json()
                except Exception:
                    data = {"raw_response": await resp.text()}

                if resp.status != 200:
                    raise RuntimeError(
                        f"LMRouter image API → {resp.status}: {data}"
                    )

                items = data.get("data")
                if not items:
                    raise RuntimeError(
                        f"LMRouter вернул неожиданный ответ: {data}"
                    )

                item = items[0]

                if item.get("url"):
                    return ImageResult(kind="url", data=item["url"])

                if item.get("b64_json"):
                    return ImageResult(kind="b64", data=item["b64_json"])

                raise RuntimeError(
                    f"В ответе LMRouter нет url или b64_json: {data}"
                )

        except aiohttp.ClientError as e:
            raise RuntimeError(f"Ошибка соединения с LMRouter: {e}") from e
