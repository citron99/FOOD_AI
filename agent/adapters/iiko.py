"""Адаптер номенклатуры iiko (Россия) → инвентарь SmartKitchen Family.

iiko — один из двух лидеров автоматизации общепита в России (вместе с
r_keeper). Адаптер читает номенклатуру через iikoCloud API
(POST /api/1/nomenclature). Документация: API iiko, раздел «Номенклатура».

Подключение (секреты — только из окружения):
- IIKO_BASE_URL — по умолчанию https://api-ru.iiko.services
- IIKO_API_TOKEN — долгоживущий токен из бэк-офиса iiko (Integrations → API keys)

Ограничения заготовки: только чтение; остатки и сроки годности не импортируются
(quantity = 0, позиции уходят в «ignored» до ручного подтверждения).
Модуль — тонкая конфигурация общей фабрики ``make_rest_source`` (base.py).

Формат тела запроса (organizationIds) зависит от настройки организации в
iiko; по умолчанию отправляется пустой объект, что возвращает номенклатуру
всех доступных организаций токена.
"""
from __future__ import annotations

from agent.adapters.base import make_rest_source

SYSTEM = "iiko"

# Ответ /api/1/nomenclature: {"products": [{"id","name","mainUnit",...}]};
# mainUnit может быть строкой или объектом с полем «name».
NAME_FIELD = "name"
UNIT_FIELD = "mainUnit"

import_products = make_rest_source(
    "iiko",
    base_url_env="IIKO_BASE_URL",
    token_env="IIKO_API_TOKEN",
    endpoint="/api/1/nomenclature",
    default_base_url="https://api-ru.iiko.services",
    body_kind="json-empty",
    items_key="products",
    unit_nested=True,
    unit_field=UNIT_FIELD,
    location="iiko (номенклатура)",
)
