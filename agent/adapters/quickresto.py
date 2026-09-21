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
"""
from __future__ import annotations

import base64
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

SYSTEM = "quickresto"

# Имена полей в ответе /api/products (проверить по документации версии API).
NAME_FIELD = "name"
UNIT_FIELD = "unit"


def _config_from_env() -> RestConfig:
    base_url = os.environ.get("QR_BASE_URL", "").rstrip("/")
    username = os.environ.get("QR_USER", "")
    password = os.environ.get("QR_PASSWORD", "")
    missing = [
        name for name, value in (
            ("QR_BASE_URL", base_url), ("QR_USER", username), ("QR_PASSWORD", password),
        ) if not value
    ]
    if missing:
        raise AdapterResponseError(
            "Не заданы переменные окружения для QuickResto: " + ", ".join(missing)
        )
    return RestConfig(base_url=base_url, endpoint="/api/products",
                      username=username, password=password)


def _map_product(item: dict) -> Product:
    name = str(item.get(NAME_FIELD, "")).strip()
    if not name:
        raise AdapterResponseError(f"Элемент справочника QuickResto без наименования: {item}")
    return Product(
        name=name,
        quantity=0.0,
        unit=str(item.get(UNIT_FIELD) or "шт."),
        location="QuickResto (справочник)",
        expires_at=None,
        status="active",
    )


def import_products(config: RestConfig | None = None,
                    transport: Transport = http_transport) -> ImportOutcome:
    cfg = config or _config_from_env()
    headers = {}
    if cfg.username or cfg.password:
        credentials = f"{cfg.username}:{cfg.password}"
        headers["Authorization"] = "Basic " + base64.b64encode(
            credentials.encode("utf-8")).decode("ascii")
    payload = fetch_json(cfg, transport, headers=headers)
    if not isinstance(payload, list):
        raise AdapterResponseError(f"QuickResto вернул неожиданную структуру: {payload}")
    return build_outcome(payload, _map_product)
