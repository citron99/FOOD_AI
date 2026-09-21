"""Адаптер номенклатуры YUMA → инвентарь SmartKitchen Family.

YUMA — молодая экосистема автоматизации общепита (зал, кухня, склад,
маркетинг). Публичная документация API ограничена: точный endpoint и формат
ответа зависят от договорённостей с вендором. Это конфигурируемая заготовка:
endpoint и имена полей задаются через переменные окружения и перед первым
реальным подключением должны быть сверены с документацией YUMA.

Подключение (секреты — только из окружения):
- YUMA_BASE_URL — базовый URL API вашего аккаунта YUMA
- YUMA_API_TOKEN — токен доступа к API
- YUMA_ENDPOINT — endpoint справочника товаров (по умолчанию /api/v1/products)
- YUMA_NAME_FIELD, YUMA_UNIT_FIELD — имена полей в ответе (name, unit)

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

SYSTEM = "yuma"


def _config_from_env() -> RestConfig:
    base_url = os.environ.get("YUMA_BASE_URL", "").rstrip("/")
    token = os.environ.get("YUMA_API_TOKEN", "")
    missing = [name for name, value in (
        ("YUMA_BASE_URL", base_url), ("YUMA_API_TOKEN", token)) if not value]
    if missing:
        raise AdapterResponseError(
            "Не заданы переменные окружения для YUMA: " + ", ".join(missing)
        )
    return RestConfig(
        base_url=base_url,
        endpoint=os.environ.get("YUMA_ENDPOINT", "/api/v1/products"),
        token=token,
    )


def _map_product(item: dict) -> Product:
    name_field = os.environ.get("YUMA_NAME_FIELD", "name")
    unit_field = os.environ.get("YUMA_UNIT_FIELD", "unit")
    name = str(item.get(name_field, "")).strip()
    if not name:
        raise AdapterResponseError(f"Элемент справочника YUMA без наименования: {item}")
    return Product(
        name=name,
        quantity=0.0,
        unit=str(item.get(unit_field) or "шт."),
        location="YUMA (справочник)",
        expires_at=None,
        status="active",
    )


def import_products(config: RestConfig | None = None,
                    transport: Transport = http_transport) -> ImportOutcome:
    cfg = config or _config_from_env()
    payload = fetch_json(cfg, transport)
    if not isinstance(payload, list):
        raise AdapterResponseError(f"YUMA вернул неожиданную структуру: {payload}")
    return build_outcome(payload, _map_product)
