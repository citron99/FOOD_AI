"""Общая база для адаптеров внешних POS/учётных систем.

Все адаптеры следуют одним правилам (согласовано с cli_agent_design.md и ТЗ):
- секреты читаются только из окружения, никогда из CLI-флагов и репозитория;
- адаптер только читает справочник/номенклатуру, запись во внешнюю систему
  не выполняется;
- остатки и сроки годности не выдумываются: импортированные позиции получают
  quantity = 0 и попадают в «ignored» до ручного подтверждения пользователем;
- транспорт внедряется (dependency injection) — адаптеры тестируются без сети.
"""
from __future__ import annotations

import json
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Callable

from agent.cli import Product

Transport = Callable[[str, bytes | None, dict[str, str], float], bytes]


class AdapterError(Exception):
    """Базовая ошибка адаптера с понятным сообщением для пользователя."""


class AdapterAuthError(AdapterError):
    """Ошибка аутентификации (неверный токен/логин/пароль)."""


class AdapterResponseError(AdapterError):
    """Некорректный, пустой или неожиданный ответ внешней системы."""


def http_transport(url: str, body: bytes | None, headers: dict[str, str], timeout: float) -> bytes:
    """Транспорт по умолчанию: один HTTP-запрос через стандартную urllib."""
    request = urllib.request.Request(url, data=body, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.read()
    except urllib.error.HTTPError as exc:
        if exc.code in (401, 403):
            raise AdapterAuthError(
                f"Внешняя система отклонила учётные данные ({url}, HTTP {exc.code})"
            ) from None
        raise AdapterError(f"Внешняя система вернула HTTP {exc.code} для {url}") from None
    except urllib.error.URLError as exc:
        raise AdapterError(f"Не удалось подключиться к внешней системе ({url}): {exc.reason}") from None


@dataclass(frozen=True)
class RestConfig:
    """Параметры REST-подключения; секреты — только из окружения."""

    base_url: str
    endpoint: str
    token: str = ""
    username: str = ""
    password: str = ""
    timeout: float = 10.0
    extra: dict[str, str] = field(default_factory=dict)

    @property
    def url(self) -> str:
        return self.base_url.rstrip("/") + "/" + self.endpoint.lstrip("/")

    def auth_headers(self) -> dict[str, str]:
        headers: dict[str, str] = {}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        return headers


def fetch_json(config: RestConfig, transport: Transport = http_transport,
               body: bytes | None = None, headers: dict[str, str] | None = None) -> Any:
    """Выполняет запрос и разбирает JSON-ответ с понятными ошибками."""
    all_headers = {"Accept": "application/json", **config.auth_headers(), **(headers or {})}
    raw = transport(config.url, body, all_headers, config.timeout)
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise AdapterResponseError(f"Внешняя система вернула некорректный JSON: {exc}") from None
    return payload


@dataclass
class ImportOutcome:
    """Результат импорта: позиции и статистика пропусков."""

    products: list[Product]
    total: int
    skipped: int  # позиции, не прошедшие маппинг (без наименования и т.п.)

    @property
    def imported(self) -> int:
        return len(self.products)


def build_outcome(items: list[Any], map_one: Callable[[Any], Product]) -> ImportOutcome:
    """Маппит список сырых элементов, считая пропущенные позиции."""
    products: list[Product] = []
    skipped = 0
    for item in items:
        try:
            products.append(map_one(item))
        except AdapterResponseError:
            skipped += 1
    return ImportOutcome(products=products, total=len(items), skipped=skipped)
