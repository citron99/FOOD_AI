"""Адаптер DeepSeek API для SmartKitchen Family.

DeepSeek — внешний LLM-провайдер. Согласно ТЗ, LLM-результаты НЕ применяются
автоматически: адаптер только формирует подсказки (идеи блюд из продуктов с
истекающим сроком), которые пользователь подтверждает вручную. Детерминированная
логика отчёта (сроки годности, группы) от LLM не зависит.

Подключение (секреты — только из окружения):
- DEEPSEEK_API_KEY — ключ из кабинета DeepSeek (platform.deepseek.com)
- DEEPSEEK_BASE_URL — по умолчанию https://api.deepseek.com
- DEEPSEEK_MODEL — по умолчанию deepseek-chat

Транспорт внедряется (dependency injection) — тесты работают без сети и без ключа.
"""
from __future__ import annotations

import json
import os

from agent.adapters.base import (
    AdapterAuthError,
    AdapterError,
    AdapterResponseError,
    Transport,
    fetch_json,
    http_transport,
)
from agent.cli import Product

SYSTEM = "deepseek"
DEFAULT_BASE_URL = "https://api.deepseek.com"
DEFAULT_MODEL = "deepseek-chat"


def chat(messages: list[dict[str, str]], *, api_key: str | None = None,
         base_url: str = DEFAULT_BASE_URL, model: str = DEFAULT_MODEL,
         max_tokens: int = 800, temperature: float = 0.3,
         transport: Transport = http_transport) -> str:
    """Отправляет диалог в /chat/completions и возвращает текст ответа."""
    key = api_key or os.environ.get("DEEPSEEK_API_KEY", "")
    if not key:
        raise AdapterError(
            "Не задана переменная окружения DEEPSEEK_API_KEY "
            "(ключ из кабинета DeepSeek, platform.deepseek.com)."
        )
    from agent.adapters.base import RestConfig
    cfg = RestConfig(base_url=base_url, endpoint="/chat/completions", token=key)
    body = json.dumps({
        "model": model,
        "messages": messages,
        "max_tokens": max_tokens,
        "temperature": temperature,
    }).encode("utf-8")
    payload = fetch_json(cfg, transport, body=body,
                         headers={"Content-Type": "application/json"})
    choices = payload.get("choices") if isinstance(payload, dict) else None
    if not choices:
        raise AdapterResponseError(f"DeepSeek вернул ответ без «choices»: {payload}")
    content = choices[0].get("message", {}).get("content")
    if not content:
        raise AdapterResponseError(f"DeepSeek вернул пустой текст ответа: {payload}")
    return str(content)


def suggest_menu_ideas(products: list[Product], *, api_key: str | None = None,
                       base_url: str = DEFAULT_BASE_URL, model: str = DEFAULT_MODEL,
                       transport: Transport = http_transport) -> str:
    """Просит DeepSeek предложить блюда из списка продуктов.

    Результат — необрабатываемая напрямую подсказка: по ТЗ её должен
    подтвердить пользователь перед использованием.
    """
    if not products:
        return "Нет продуктов для подсказок."
    names = ", ".join(p.name for p in products)
    prompt = (
        f"Есть продукты: {names}. Предложи 3–5 простых блюд, которые можно "
        "приготовить преимущественно из этих продуктов, чтобы использовать их "
        "быстрее. Ответь кратким списком на русском: название блюда — 1 строка "
        "состава. Без вступлений."
    )
    return chat(
        [{"role": "user", "content": prompt}],
        api_key=api_key, base_url=base_url, model=model,
        transport=transport,
    )
