"""Адаптер товаров FrontPad → инвентарь SmartKitchen Family.

FrontPad — облачная POS/CRM для кафе и ресторанов, популярная у небольших
заведений. Адаптер читает справочник товаров через API FrontPad
(secret-ключ, POST-запрос с параметром «команда»). Конкретный набор команд
и полей зависит от тарифа и документации FrontPad — они вынесены в константы.

Подключение (секреты — только из окружения):
- FRONTPAD_BASE_URL — например https://<ваш-домен>.frontpad.ru
- FRONTPAD_SECRET — секретный ключ API из личного кабинета FrontPad

Ограничения заготовки: только чтение; остатки и сроки годности не импортируются
(quantity = 0, позиции уходят в «ignored» до ручного подтверждения).
"""
from __future__ import annotations

import json
import os
import urllib.parse

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

SYSTEM = "frontpad"

# Команда API и имена полей (проверить по документации FrontPad для вашего тарифа).
COMMAND = "getProducts"
NAME_FIELD = "name"
UNIT_FIELD = "unit"


def _config_from_env() -> RestConfig:
    base_url = os.environ.get("FRONTPAD_BASE_URL", "").rstrip("/")
    secret = os.environ.get("FRONTPAD_SECRET", "")
    missing = [name for name, value in (
        ("FRONTPAD_BASE_URL", base_url), ("FRONTPAD_SECRET", secret)) if not value]
    if missing:
        raise AdapterResponseError(
            "Не заданы переменные окружения для FrontPad: " + ", ".join(missing)
        )
    return RestConfig(base_url=base_url, endpoint="/api/", token=secret)


def _map_product(item: dict) -> Product:
    name = str(item.get(NAME_FIELD, "")).strip()
    if not name:
        raise AdapterResponseError(f"Элемент справочника FrontPad без наименования: {item}")
    return Product(
        name=name,
        quantity=0.0,
        unit=str(item.get(UNIT_FIELD) or "шт."),
        location="FrontPad (справочник)",
        expires_at=None,
        status="active",
    )


def import_products(config: RestConfig | None = None,
                    transport: Transport = http_transport) -> ImportOutcome:
    cfg = config or _config_from_env()
    params = urllib.parse.urlencode({"secret": cfg.token, "cmd": COMMAND}).encode("utf-8")
    payload = fetch_json(cfg, transport, body=params,
                         headers={"Content-Type": "application/x-www-form-urlencoded"})
    if not isinstance(payload, list):
        raise AdapterResponseError(f"FrontPad вернул неожиданную структуру: {payload}")
    return build_outcome(payload, _map_product)
