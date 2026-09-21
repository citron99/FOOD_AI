"""Адаптер номенклатуры Saby (СБИС) Presto → инвентарь SmartKitchen Family.

Saby Presto — экосистема автоматизации ресторанного бизнеса от «Тензора»
(бывший СБИС Presto). Публичная документация API для внешней номенклатуры
ограничена: точный endpoint и формат ответа зависят от договорённостей
с «Тензором». Поэтому это конфигурируемая заготовка: endpoint и имена полей
задаются через переменные окружения и перед первым реальным подключением
должны быть сверены с документацией Saby.

Подключение (секреты — только из окружения):
- SABY_BASE_URL — базовый URL API вашего аккаунта Saby
- SABY_API_TOKEN — токен доступа к API
- SABY_ENDPOINT — endpoint справочника товаров (по умолчанию /api/v1/products)
- SABY_NAME_FIELD, SABY_UNIT_FIELD — имена полей в ответе (name, unit)

Ограничения заготовки: только чтение; остатки и сроки годности не импортируются
(quantity = 0, позиции уходят в «ignored» до ручного подтверждения).
"""
from __future__ import annotations

import os

from agent.adapters.base import (
    AdapterResponseError,
    ImportOutcome,
    RestConfig,
    Transport,
    build_outcome,
    fetch_json,
    http_transport,
)
from agent.cli import Product

SYSTEM = "saby"


def _config_from_env() -> RestConfig:
    base_url = os.environ.get("SABY_BASE_URL", "").rstrip("/")
    token = os.environ.get("SABY_API_TOKEN", "")
    missing = [name for name, value in (
        ("SABY_BASE_URL", base_url), ("SABY_API_TOKEN", token)) if not value]
    if missing:
        raise AdapterResponseError(
            "Не заданы переменные окружения для Saby: " + ", ".join(missing)
        )
    return RestConfig(
        base_url=base_url,
        endpoint=os.environ.get("SABY_ENDPOINT", "/api/v1/products"),
        token=token,
    )


def _map_product(item: dict) -> Product:
    name_field = os.environ.get("SABY_NAME_FIELD", "name")
    unit_field = os.environ.get("SABY_UNIT_FIELD", "unit")
    name = str(item.get(name_field, "")).strip()
    if not name:
        raise AdapterResponseError(f"Элемент справочника Saby без наименования: {item}")
    return Product(
        name=name,
        quantity=0.0,
        unit=str(item.get(unit_field) or "шт."),
        location="Saby Presto (справочник)",
        expires_at=None,
        status="active",
    )


def import_products(config: RestConfig | None = None,
                    transport: Transport = http_transport) -> ImportOutcome:
    cfg = config or _config_from_env()
    payload = fetch_json(cfg, transport)
    if not isinstance(payload, list):
        raise AdapterResponseError(f"Saby вернул неожиданную структуру: {payload}")
    return build_outcome(payload, _map_product)
