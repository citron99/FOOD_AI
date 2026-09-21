"""Адаптер номенклатуры iiko (Россия) → инвентарь SmartKitchen Family.

iiko — один из двух лидеров автоматизации общепита в России (вместе с
r_keeper). Адаптер читает номенклатуру через iikoCloud API
(POST /api/1/nomenclature). Документация: API iiko, раздел «Номенклатура».

Подключение (секреты — только из окружения):
- IIKO_BASE_URL — по умолчанию https://api-ru.iiko.services
- IIKO_API_TOKEN — долгоживущий токен из бэк-офиса iiko (Integrations → API keys)

Ограничения заготовки: только чтение; остатки и сроки годности не импортируются
(quantity = 0, позиции уходят в «ignored» до ручного подтверждения).
"""
from __future__ import annotations

import json
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

SYSTEM = "iiko"


def _config_from_env() -> RestConfig:
    token = os.environ.get("IIKO_API_TOKEN", "")
    if not token:
        raise AdapterResponseError(
            "Не задана переменная окружения IIKO_API_TOKEN "
            "(долгоживущий токен из бэк-офиса iiko)."
        )
    return RestConfig(
        base_url=os.environ.get("IIKO_BASE_URL", "https://api-ru.iiko.services"),
        endpoint="/api/1/nomenclature",
        token=token,
    )


def _map_product(item: dict) -> Product:
    # Ответ /api/1/nomenclature: {"products": [{"id","name","mainUnit",...}]}
    name = str(item.get("name", "")).strip()
    if not name:
        raise AdapterResponseError(f"Элемент номенклатуры без наименования: {item}")
    unit = ""
    main_unit = item.get("mainUnit")
    if isinstance(main_unit, str):
        unit = main_unit
    elif isinstance(main_unit, dict):
        unit = str(main_unit.get("name", ""))
    return Product(
        name=name,
        quantity=0.0,
        unit=unit or "шт.",
        location="iiko (номенклатура)",
        expires_at=None,
        status="active",
    )


def import_products(config: RestConfig | None = None,
                    transport: Transport = http_transport) -> ImportOutcome:
    """Запрашивает номенклатуру iiko и маппит её на Product.

    Формат тела запроса (organizationIds) зависит от настройки организации в
    iiko; по умолчанию отправляется пустой объект, что возвращает номенклатуру
    всех доступных организаций токена.
    """
    cfg = config or _config_from_env()
    body = json.dumps({}).encode("utf-8")
    payload = fetch_json(cfg, transport, body=body,
                         headers={"Content-Type": "application/json"})
    if not isinstance(payload, dict) or "products" not in payload:
        raise AdapterResponseError(f"iiko вернул неожиданную структуру номенклатуры: {payload}")
    items = payload["products"]
    if not isinstance(items, list):
        raise AdapterResponseError("Поле «products» в ответе iiko должно быть списком")
    return build_outcome(items, _map_product)
