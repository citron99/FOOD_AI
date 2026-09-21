"""Адаптер справочника товаров R:keeper (UCS) → инвентарь SmartKitchen Family.

Дизайн-заготовка (stub): реализованы конфигурация подключения, HTTP-транспорт
к XML-интерфейсу R:keeper, безопасный маппинг справочника товаров на
``agent.cli.Product`` и обработка ошибок. Транспорт внедряется (dependency
injection), поэтому адаптер тестируется без сети и без ключа R:keeper.

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
"""
from __future__ import annotations

import base64
import os
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from typing import Any, Callable

from agent.cli import Product, parse_date

Transport = Callable[[str, bytes, dict[str, str], float], bytes]

# Запрос справочника товаров в XML-интерфейсе R:keeper (RK7 API).
# Версия протокола и набор атрибутов зависят от установки — см. RkeeperConfig.
_PRODUCTS_QUERY = (
    '<RK7Query><RK7CMD CMD="GetRefData" RefName="{ref}" '
    'IgnoreInactive="1" WithChildItems="0"/></RK7Query>'
)


class RkeeperError(Exception):
    """Базовая ошибка адаптера R:keeper с понятным сообщением для пользователя."""


class RkeeperAuthError(RkeeperError):
    """Ошибка аутентификации (неверные учётные данные интерфейса)."""


class RkeeperResponseError(RkeeperError):
    """Некорректный или пустой ответ R:keeper."""


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

    @classmethod
    def from_env(cls) -> "RkeeperConfig":
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


def _http_post(url: str, body: bytes, headers: dict[str, str], timeout: float) -> bytes:
    """Транспорт по умолчанию: один POST через стандартную библиотеку urllib."""
    request = urllib.request.Request(url, data=body, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.read()
    except urllib.error.HTTPError as exc:
        if exc.code == 401:
            raise RkeeperAuthError(
                f"R:keeper отклонил учётные данные интерфейса «{url}» (HTTP 401)"
            ) from None
        raise RkeeperError(f"R:keeper вернул HTTP {exc.code} для {url}") from None
    except urllib.error.URLError as exc:
        raise RkeeperError(f"Не удалось подключиться к R:keeper ({url}): {exc.reason}") from None


def _authorization_header(config: RkeeperConfig) -> str:
    credentials = f"{config.station}\\{config.username}:{config.password}"
    return "Basic " + base64.b64encode(credentials.encode("utf-8")).decode("ascii")


def fetch_product_directory(
    config: RkeeperConfig,
    transport: Transport = _http_post,
) -> list[dict[str, Any]]:
    """Запрашивает справочник товаров и возвращает сырые атрибуты элементов."""
    body = _PRODUCTS_QUERY.format(ref=config.ref_name).encode("utf-8")
    headers = {
        "Content-Type": "application/xml; charset=utf-8",
        "Authorization": _authorization_header(config),
    }
    url = f"{config.base_url}/rk7api/v1"
    raw = transport(url, body, headers, config.timeout)
    return _parse_directory(raw, config)


def _parse_directory(raw: bytes, config: RkeeperConfig) -> list[dict[str, Any]]:
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


@dataclass
class ImportResult:
    """Результат импорта справочника: позиции и статистика пропусков."""

    products: list[Product]
    total: int
    skipped: int  # позиции, не прошедшие маппинг (без наименования и т.п.)

    @property
    def imported(self) -> int:
        return len(self.products)


def import_products(
    config: RkeeperConfig,
    transport: Transport = _http_post,
) -> ImportResult:
    """Полный сценарий импорта: запрос → маппинг → ImportResult.

    Позиции с ошибками маппинга пропускаются, но их число фиксируется
    в ``result.skipped`` для прозрачности аудита.
    """
    items = fetch_product_directory(config, transport)
    products: list[Product] = []
    skipped = 0
    for item in items:
        try:
            products.append(map_rk_item(item, config))
        except RkeeperError:
            skipped += 1
    return ImportResult(products=products, total=len(items), skipped=skipped)
