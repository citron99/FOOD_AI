"""Адаптер DeepSeek API для SmartKitchen Family.

DeepSeek — внешний LLM-провайдер. Согласно ТЗ, LLM-результаты НЕ применяются
автоматически: адаптер только формирует подсказки (идеи блюд из продуктов с
истекающим сроком), которые пользователь подтверждает вручную. Детерминированная
логика отчёта (сроки годности, группы) от LLM не зависит.

Аллергены профиля семьи уходят прямо в промпт, но это лишь первое сито: ответ
всё равно проходит через детерминированный hard-filter ``agent.allergens``,
поэтому непослушная модель не приведёт к публикации опасного блюда.

Подключение (секреты — только из окружения):
- DEEPSEEK_API_KEY — ключ из кабинета DeepSeek (platform.deepseek.com)
- DEEPSEEK_BASE_URL — по умолчанию https://api.deepseek.com
- DEEPSEEK_MODEL — по умолчанию deepseek-chat
- DEEPSEEK_TIMEOUT — таймаут запроса в секундах, по умолчанию 30.0
  (10 секунд дефолта RestConfig для chat-completion с max_tokens=800 мало)

Приоритет настроек: явный аргумент функции > переменная окружения > значение
по умолчанию. Транспорт внедряется (dependency injection) — тесты работают
без сети и без ключа.
"""
from __future__ import annotations

import json
import os
from collections.abc import Iterable

from agent.adapters.base import (
    AdapterError,
    AdapterResponseError,
    RestConfig,
    Transport,
    fetch_json,
    http_transport,
)
from agent.allergens import normalize
from agent.models import Product

SYSTEM = "deepseek"
DEFAULT_BASE_URL = "https://api.deepseek.com"
DEFAULT_MODEL = "deepseek-chat"
DEFAULT_TIMEOUT = 30.0


def _resolve_timeout() -> float:
    """Таймаут из DEEPSEEK_TIMEOUT; молча игнорировать опечатку нельзя."""
    raw = os.environ.get("DEEPSEEK_TIMEOUT", "").strip()
    if not raw:
        return DEFAULT_TIMEOUT
    try:
        value = float(raw)
    except ValueError:
        raise AdapterError(
            f"DEEPSEEK_TIMEOUT должно быть числом секунд, получено: {raw!r}"
        ) from None
    if value <= 0:
        raise AdapterError("DEEPSEEK_TIMEOUT должно быть положительным числом секунд")
    return value


def chat(messages: list[dict[str, str]], *, api_key: str | None = None,
         base_url: str | None = None, model: str | None = None,
         max_tokens: int = 800, temperature: float = 0.3,
         timeout: float | None = None,
         transport: Transport = http_transport) -> str:
    """Отправляет диалог в /chat/completions и возвращает текст ответа."""
    key = api_key or os.environ.get("DEEPSEEK_API_KEY", "")
    if not key:
        raise AdapterError(
            "Не задана переменная окружения DEEPSEEK_API_KEY "
            "(ключ из кабинета DeepSeek, platform.deepseek.com)."
        )
    # П.8 ревью: BASE_URL/MODEL задокументированы в .env.example, но не читались.
    base_url = base_url or os.environ.get("DEEPSEEK_BASE_URL") or DEFAULT_BASE_URL
    model = model or os.environ.get("DEEPSEEK_MODEL") or DEFAULT_MODEL
    if timeout is None:
        timeout = _resolve_timeout()
    cfg = RestConfig(base_url=base_url, endpoint="/chat/completions", token=key,
                     timeout=timeout)
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
                       base_url: str | None = None, model: str | None = None,
                       allergens: Iterable[str] | None = None,
                       timeout: float | None = None,
                       transport: Transport = http_transport) -> str:
    """Просит DeepSeek предложить блюда из списка продуктов.

    ``allergens`` — аллергены из профиля семьи. Они попадают прямо в промпт,
    чтобы модель не предлагала опасные блюда вовсе: детерминированный
    hard-filter (``agent.allergens``) режет выдачу постфактум, и без подсказки
    в промпте это кончается пустым списком вместо полезных идей.

    Результат — необрабатываемая напрямую подсказка: по ТЗ её должен
    подтвердить пользователь перед использованием, а hard-filter всё равно
    применяется к ответу независимо от того, послушалась модель или нет.
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
    forbidden = sorted({normalize(item).strip() for item in (allergens or [])
                        if str(item).strip()})
    if forbidden:
        prompt += (
            " СТРОГОЕ ОГРАНИЧЕНИЕ ПО ЗДОРОВЬЮ: в семье аллергия на "
            + ", ".join(forbidden)
            + ". Не предлагай блюда, содержащие эти продукты, их производные "
            "и следы (включая соусы, панировку и специи). В составе каждого "
            "блюда перечисли все ингредиенты, чтобы ограничение можно было "
            "проверить."
        )
    return chat(
        [{"role": "user", "content": prompt}],
        api_key=api_key, base_url=base_url, model=model,
        timeout=timeout,
        transport=transport,
    )
