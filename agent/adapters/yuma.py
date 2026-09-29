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
Модуль — тонкая конфигурация общей фабрики ``make_rest_source`` (base.py).
"""
from __future__ import annotations

from agent.adapters.base import make_rest_source

SYSTEM = "yuma"

import_products = make_rest_source(
    "YUMA",
    base_url_env="YUMA_BASE_URL",
    token_env="YUMA_API_TOKEN",
    endpoint="/api/v1/products",
    endpoint_env="YUMA_ENDPOINT",
    name_field_env="YUMA_NAME_FIELD",
    unit_field_env="YUMA_UNIT_FIELD",
    location="YUMA (справочник)",
)
