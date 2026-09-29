"""Адаптер товаров QuickResto → инвентарь SmartKitchen Family.

QuickResto — российская система автоматизации общепита (входит в реестр
отечественного ПО). Адаптер читает справочник товаров через REST API
QuickResto (HTTP Basic-авторизация, GET /api/products). Точные имена полей
зависят от версии API — они вынесены в константы модуля.

Подключение (секреты — только из окружения):
- QR_BASE_URL — например https://<ваш-домен>.quickresto.ru
- QR_USER, QR_PASSWORD — учётная запись с доступом к API

Ограничения заготовки: только чтение; остатки и сроки годности не импортируются
(quantity = 0, позиции уходят в «ignored» до ручного подтверждения).
Модуль — тонкая конфигурация общей фабрики ``make_rest_source`` (base.py);
заголовок Basic собирает ``RestConfig.auth_headers()``.
"""
from __future__ import annotations

from agent.adapters.base import make_rest_source

SYSTEM = "quickresto"

# Имена полей в ответе /api/products (проверить по документации версии API).
NAME_FIELD = "name"
UNIT_FIELD = "unit"

import_products = make_rest_source(
    "QuickResto",
    base_url_env="QR_BASE_URL",
    username_env="QR_USER",
    password_env="QR_PASSWORD",
    endpoint="/api/products",
    auth_kind="basic",
    location="QuickResto (справочник)",
)
