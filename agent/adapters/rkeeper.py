"""Адаптер справочника товаров R:keeper (UCS) → инвентарь SmartKitchen Family.

Дизайн-заготовка (stub): реализованы конфигурация подключения, HTTP-транспорт
к XML-интерфейсу R:keeper, безопасный маппинг справочника товаров на
``agent.models.Product`` и обработка ошибок. Транспорт внедряется (dependency
injection), поэтому адаптер тестируется без сети и без ключа R:keeper.

Волна 5 (ревью, п.12): раньше модуль держал параллельную иерархию — свои
исключения, свой ``ImportResult``, свой транспорт. Теперь ошибки наследуются
от общих ``AdapterError``/``AdapterAuthError``/``AdapterResponseError``
(имена ``RkeeperError`` и т.п. сохранены для совместимости), результат импорта
— общий ``ImportOutcome``, транспорт и заголовок Basic — из ``base.py``.

Важные ограничения (зафиксированы намеренно):
- Справочник товаров R:keeper не содержит сроков годности и домашних остатков.
  Адаптер по умолчанию отдаёт ``quantity = 0`` и ``expires_at = None``,
  поэтому списанные таким образом позиции не попадают в активный учёт
  (``classify`` вернёт "ignore") до ручного подтверждения пользователем.
- Адаптер только читает справочник (GetRefData). Запись в R:keeper из MVP
  не выполняется — это соответствует границам ТЗ (без автоматических покупок
  и записи без подтверждения).
- Параметры запроса (имя справочника, имена атрибутов) вынесены в конфигурацию:
  они зависят от версии R:keeper и настройки конкретного объекта.
- Защита XML-парсера (п.17 ревью): ответы с объявлением DOCTYPE отклоняются
  до парсинга (внутренние сущности раскрывались бы стандартным ElementTree —
  вектор «billion laughs»), размер ответа ограничен ``max_response_bytes``.
"""
from __future__ import annotations

import os
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from functools import partial
from typing import Any

from agent.adapters.base import (
    AdapterAuthError,
    AdapterError,
    AdapterResponseError,
    ImportOutcome,
    Transport,
    basic_authorization,
    build_outcome,
    http_transport,
)
from agent.models import Product, parse_date

# Запрос справочника товаров в XML-интерфейсе R:keeper (RK7 API).
# Версия протокола и набор атрибутов зависят от установки — см. RkeeperConfig.
_PRODUCTS_QUERY = (
    '<RK7Query><RK7CMD CMD="GetRefData" RefName="{ref}" '
    'IgnoreInactive="1" WithChildItems="0"/></RK7Query>'
)


class RkeeperError(AdapterError):
    """Базовая ошибка адаптера R:keeper с понятным сообщением для пользователя."""


class RkeeperAuthError(RkeeperError, AdapterAuthError):
    """Ошибка аутентификации (неверные учётные данные интерфейса)."""


class RkeeperResponseError(RkeeperError, AdapterResponseError):
    """Некорректный или пустой ответ R:keeper."""


# Обратная совместимость (волна 5, п.12): прежний собственный ``ImportResult``
# заменён общим ``ImportOutcome`` из base.py; алиас сохраняет старые импорты.
ImportResult = ImportOutcome


@dataclass(frozen=True)
class RkeeperConfig:
    """Параметры подключения к XML-интерфейсу R:keeper.

    Значения читаются из окружения (см. ``from_env``), секреты — только из
    окружения, никогда не из CLI-флагов и не из файлов репозитория
    (согласно принципам cli_agent_design.md).
    """

    base_url: str                      # например http://192.168.0.10:8080
    station: str                       # имя интерфейса/станции RK7
    username: str
    password: str
    ref_name: str = "Products"         # имя справочника в RK7 API
    item_tag: str = "Item"             # тег элемента справочника в ответе
    name_attr: str = "Name"            # атрибут с наименованием товара
    code_attr: str = "Code"            # атрибут с кодом товара
    unit_attr: str = "UnitName"        # атрибут с единицей измерения
    timeout: float = 10.0
    max_response_bytes: int = 10 * 1024 * 1024  # защита от переполнения ответом

    @classmethod
    def from_env(cls) -> RkeeperConfig:
        base_url = os.environ.get("RK7_BASE_URL", "").rstrip("/")
        username = os.environ.get("RK7_USER", "")
        password = os.environ.get("RK7_PASSWORD", "")
        station = os.environ.get("RK7_STATION", "")
        missing = [
            name for name, value in (
                ("RK7_BASE_URL", base_url), ("RK7_USER", username),
                ("RK7_PASSWORD", password), ("RK7_STATION", station),
            ) if not value
        ]
        if missing:
            raise RkeeperError(
                "Не заданы переменные окружения для R:keeper: "
                + ", ".join(missing)
                + ". Укажите их перед запуском импорта справочника."
            )
        return cls(base_url=base_url, station=station, username=username, password=password)


def _authorization_header(config: RkeeperConfig) -> str:
    credentials = f"{config.station}\\{config.username}:{config.password}"
    return basic_authorization(credentials)


def fetch_product_directory(
    config: RkeeperConfig,
    transport: Transport = http_transport,
) -> list[dict[str, Any]]:
    """Запрашивает справочник товаров и возвращает сырые атрибуты элементов."""
    body = _PRODUCTS_QUERY.format(ref=config.ref_name).encode("utf-8")
    headers = {
        "Content-Type": "application/xml; charset=utf-8",
        "Authorization": _authorization_header(config),
    }
    url = f"{config.base_url}/rk7api/v1"
    raw = transport(url, body, headers, config.timeout)
    if len(raw) > config.max_response_bytes:
        raise RkeeperResponseError(
            f"Ответ R:keeper слишком велик: {len(raw)} байт "
            f"(лимит {config.max_response_bytes}). Возможно, справочник избыточен — "
            "уточните RefName/IgnoreInactive или увеличьте max_response_bytes."
        )
    return _parse_directory(raw, config)


# XML-интерфейс RK7 не использует DTD. Любое объявление DOCTYPE в ответе —
# признак вредоносной нагрузки (внутренние сущности: «billion laughs»; внешние
# сущности: XXE). Сканируем до парсинга: стандартный ElementTree внутренние
# сущности раскрывает (п.17 ревью), defusedxml проект не подключает (чистая
# стандартная библиотека), поэтому отклоняем такие ответы целиком.
_DOCTYPE_RE = re.compile(rb"<!DOCTYPE", re.IGNORECASE)


def _parse_directory(raw: bytes, config: RkeeperConfig) -> list[dict[str, Any]]:
    if _DOCTYPE_RE.search(raw):
        raise RkeeperResponseError(
            "Ответ R:keeper содержит объявление DOCTYPE, которое XML-интерфейс "
            "RK7 не использует. Ответ отклонён из соображений безопасности "
            "(внутренние/внешние сущности XML)."
        )
    try:
        root = ET.fromstring(raw)
    except ET.ParseError as exc:
        raise RkeeperResponseError(f"R:keeper вернул некорректный XML: {exc}") from None
    if root.get("Status") == "QueryError":
        raise RkeeperResponseError(
            f"R:keeper отклонил запрос справочника «{config.ref_name}»: "
            f"{root.get('ErrorText', 'причина не указана')}"
        )
    items = [dict(element.attrib) for element in root.iter(config.item_tag)]
    if not items:
        raise RkeeperResponseError(
            f"Справочник «{config.ref_name}» пуст или не содержит тегов «{config.item_tag}»"
        )
    return items


def map_rk_item(item: dict[str, Any], config: RkeeperConfig) -> Product:
    """Маппит один элемент справочника R:keeper на Product.

    Остаток и срок годности намеренно не импортируются: в справочнике
    R:keeper их нет, а выдумывать данные нельзя (см. ограничения модуля).
    Позиция получает quantity = 0 и попадает в «ignored» до ручного учёта.
    """
    name = str(item.get(config.name_attr, "")).strip()
    if not name:
        raise RkeeperResponseError(
            f"Элемент справочника без наименования (атрибут «{config.name_attr}»): {item}"
        )
    return Product(
        name=name,
        quantity=0.0,
        unit=str(item.get(config.unit_attr) or "шт."),
        location="R:keeper (справочник)",
        expires_at=parse_date(None, name),
        status="active",
    )


def import_products(
    config: RkeeperConfig,
    transport: Transport = http_transport,
) -> ImportOutcome:
    """Полный сценарий импорта: запрос → маппинг → ImportOutcome.

    Позиции с ошибками маппинга пропускаются, но их число фиксируется
    в ``result.skipped`` для прозрачности аудита.
    """
    items = fetch_product_directory(config, transport)
    return build_outcome(items, partial(map_rk_item, config=config))
