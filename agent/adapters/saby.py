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
Модуль — тонкая конфигурация общей фабрики ``make_rest_source`` (base.py).
"""
from __future__ import annotations

from agent.adapters.base import make_rest_source

SYSTEM = "saby"

import_products = make_rest_source(
    "Saby",
    base_url_env="SABY_BASE_URL",
    token_env="SABY_API_TOKEN",
    endpoint="/api/v1/products",
    endpoint_env="SABY_ENDPOINT",
    name_field_env="SABY_NAME_FIELD",
    unit_field_env="SABY_UNIT_FIELD",
    location="Saby Presto (справочник)",
)
